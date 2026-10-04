# wsl_05_host_vhdx.ps1 - Windows-side check of the WSL2 host (run weekly/monthly from Windows).
#
#   powershell -ExecutionPolicy Bypass -File wsl_05_host_vhdx.ps1            # report only (read-only)
#   powershell -ExecutionPolicy Bypass -File wsl_05_host_vhdx.ps1 -Compact   # shrink the VHDX (STOPS WSL + all containers)
#
# Report: distro state, keepalive task present, VHDX size vs C: free space (a WSL2 VHDX only grows; if it
# fills C: the distro and live Postgres die), .wslconfig memory/swap limits.
# -Compact: runs fstrim inside WSL, `wsl --shutdown`, then Optimize-VHD (Hyper-V module) or diskpart
# `compact vdisk`, then restarts the keepalive. It refuses to run unless the live Postgres container has
# just been dumped (logical pg_dump < 6 h; -TakeBackup runs wsl_06_backup_pg.sh logical + physical first) and unless -Yes is given.
# Per CLAUDE.md: a session must never stop/recreate the live container; the owner runs -Compact by hand.
param([switch]$Compact, [switch]$Yes, [switch]$TakeBackup, [string]$Distro = 'Ubuntu', [double]$WarnFreeGB = 100, [double]$CritFreeGB = 50)
$ErrorActionPreference = 'Stop'
$status = 0
function Ok($m)   { Write-Host "[ OK ] $m" }
function Warn($m) { Write-Host "[WARN] $m"; if ($script:status -lt 1) { $script:status = 1 } }
function Crit($m) { Write-Host "[CRIT] $m"; $script:status = 2 }

# distro state
$list = (wsl.exe -l -v) -replace "`0", ''
if ($list -match "$Distro\s+Running") { Ok "distro $Distro is Running" } else { Crit "distro $Distro is NOT running - containers are down (see keepalive note in CLAUDE.md)" }

# keepalive task (name taken from the existing WSL keepalive setup; any task running 'sleep infinity' counts)
$ka = Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { ($_.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -match 'sleep infinity' }
if ($ka) { Ok "keepalive task present: $($ka.TaskName) ($($ka.State))" } else { Warn "no scheduled task keeps WSL alive (wsl -d $Distro -- bash -c 'sleep infinity')" }

# VHDX size vs free disk
$base = (Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' | ForEach-Object { Get-ItemProperty $_.PSPath } | Where-Object { $_.DistributionName -eq $Distro }).BasePath -replace '^\\\\\?\\', ''
$vhdx = Join-Path $base 'ext4.vhdx'
if (Test-Path $vhdx) {
    $sizeGB = [math]::Round((Get-Item $vhdx).Length / 1GB, 1)
    $drv = (Get-Item $vhdx).PSDrive; $freeGB = [math]::Round($drv.Free / 1GB, 1)
    Write-Host "[INFO] $vhdx = $sizeGB GB; $($drv.Name): free = $freeGB GB"
    if ($freeGB -lt $CritFreeGB) { Crit "$($drv.Name): only $freeGB GB free (< $CritFreeGB) - the VHDX can fill the disk" }
    elseif ($freeGB -lt $WarnFreeGB) { Warn "$($drv.Name): $freeGB GB free (< $WarnFreeGB)" } else { Ok "$($drv.Name): $freeGB GB free" }
    $inside = (wsl.exe -d $Distro -- df -BG --output=used / | Select-Object -Last 1).Trim() -replace 'G', ''
    if ($inside -match '^\d+$') {
        $slack = [math]::Round($sizeGB - [int]$inside, 1)
        Write-Host "[INFO] used inside WSL = $inside GB -> reclaimable slack in the VHDX ~ $slack GB"
        if ($slack -gt 50) { Warn "VHDX holds ~$slack GB of already-freed space; consider -Compact in a maintenance window" }
    }
} else { Warn "VHDX not found at $vhdx" }

$cfg = Join-Path $env:USERPROFILE '.wslconfig'
if (Test-Path $cfg) { Write-Host "[INFO] .wslconfig:"; Get-Content $cfg | ForEach-Object { "   $_" } } else { Warn ".wslconfig missing - WSL may take up to 50% of host RAM with no swap cap" }

if ($Compact) {
    if ($TakeBackup) {
        $b = '/mnt/c/desarrollo/fondos/scripts/ops/maintenance/wsl/wsl_06_backup_pg.sh'
        foreach ($m in 'logical', 'physical') { Write-Host "[ACT ] wsl_06_backup_pg.sh $m"; wsl.exe -d $Distro -u root -- bash $b $m; if ($LASTEXITCODE -ne 0) { Crit "backup '$m' failed (exit $LASTEXITCODE) - compaction aborted"; exit 2 } }
    }
    $dump = Get-ChildItem '\wsl.localhost\Ubuntu\mnt\backups\pg_fondos\dump\fondos_2*.dump' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $dump -or ((Get-Date) - $dump.LastWriteTime).TotalHours -gt 6) { Crit "-Compact refused: no fondos dump newer than 6 h. Run wsl_06_backup_pg.sh logical first, or pass -TakeBackup."; exit 2 }
    if (-not $Yes) { Crit "-Compact stops WSL and every container (live Postgres). Re-run with -Compact -Yes to proceed."; exit 2 }
    Write-Host "[ACT ] fstrim"; wsl.exe -d $Distro -u root -- fstrim -av
    Write-Host "[ACT ] wsl --shutdown"; wsl.exe --shutdown; Start-Sleep 8
    $before = (Get-Item $vhdx).Length
    if (Get-Command Optimize-VHD -ErrorAction SilentlyContinue) { Write-Host "[ACT ] Optimize-VHD"; Optimize-VHD -Path $vhdx -Mode Full }
    else {
        Write-Host "[ACT ] diskpart compact vdisk"
        $s = Join-Path $env:TEMP 'compact_wsl.txt'; "select vdisk file=`"$vhdx`"`nattach vdisk readonly`ncompact vdisk`ndetach vdisk`nexit" | Set-Content $s -Encoding ascii
        diskpart /s $s; Remove-Item $s
    }
    Write-Host ("[ACT ] VHDX {0} GB -> {1} GB" -f [math]::Round($before / 1GB, 1), [math]::Round((Get-Item $vhdx).Length / 1GB, 1))
    Write-Host "[ACT ] restarting distro + keepalive"; Start-Process wsl.exe -ArgumentList "-d $Distro -- bash -c `"sleep infinity`"" -WindowStyle Hidden
    Write-Host "Now ask the owner to verify the containers (docker ps) - fondos_postgres must be Up and healthy."
}
switch ($status) { 0 { Write-Host '=== RESULT: OK ===' } 1 { Write-Host '=== RESULT: WARNINGS ===' } default { Write-Host '=== RESULT: CRITICAL ===' } }
exit $status
