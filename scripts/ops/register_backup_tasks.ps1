# Registers the recurring Postgres backup tasks in Windows Task Scheduler (FND-0179 / FND-0073).
#
#   powershell -ExecutionPolicy Bypass -File scripts\ops\register_backup_tasks.ps1              # show what WOULD be registered
#   powershell -ExecutionPolicy Bypass -File scripts\ops\register_backup_tasks.ps1 -Register     # register the three tasks (re-registers with -Force)
#   powershell -ExecutionPolicy Bypass -File scripts\ops\register_backup_tasks.ps1 -Unregister   # remove them
#
# Policy (FND-0179, owner-approved 2026-10-02): a physical base backup every 3 days, keep the newest 2 bases,
# prune the WAL archive older than the oldest kept base right after a verified base, then mirror to the external
# disk D: daily. At ~18 GB/day of WAL this keeps <= ~6 days of WAL + 2 bases (~190 GB) on each side.
#
#   Task 1  "Fondos PG basebackup+prune"  every 3 days 02:00
#           setup_wal_archive.sh basebackup && setup_wal_archive.sh prune --yes   (runs as root in WSL)
#           prune only runs if the base backup succeeded and verified (&&); it deletes nothing until 2 bases exist.
#   Task 3  "Fondos PG logical dump"      daily 03:30   (FND-0290, 2026-10-17)
#           backup_live_pg.sh   (pg_dump -Fc of fondos + gestion + roles into /mnt/backups/pg_fondos/dump, keep 7 sets)
#           Independent of the external disk. Before this task the ONLY logical dump was step (2) of backup_offbox.sh, which comes after
#           its D: mount guard: with D: detached no dump was taken at all (the newest dumps were 305 h old on 2026-10-17 while the
#           physical base backups kept running). It runs before Task 2 so the copy has a fresh set.
#   Task 2  "Fondos PG offbox copy"       daily 04:30
#           backup_offbox.sh --no-dump --mirror-prune   (copy the newest local dump set + SHA-256, PITR rsync, then mirrors the
#           source's deletions to D:). It no longer takes its own dump (Task 3 does): two sets a day would use up the 7 retained
#           sets in 3.5 days. If Task 3 stops running, pg_05_backup_verify reports the dump age (WARN 36 h, CRITICAL 72 h).
#
# Both run as the current user while logged on (same as the existing WSL keepalive task) and catch up after a
# missed start. Nothing here touches the database; the tasks do, when they fire.
param([switch]$Register, [switch]$Unregister)

$ErrorActionPreference = 'Stop'
$repoWsl = '/mnt/c/desarrollo/fondos/scripts/ops'
$tasks = @(
    @{ Name = 'Fondos PG basebackup+prune'; Days = 3; At = '02:00';
       Args = "-d Ubuntu -u root -- bash -c ""bash $repoWsl/setup_wal_archive.sh basebackup && bash $repoWsl/setup_wal_archive.sh prune --yes""" },
    @{ Name = 'Fondos PG logical dump'; Days = 1; At = '03:30';
       Args = "-d Ubuntu -u root -- bash $repoWsl/backup_live_pg.sh" },
    @{ Name = 'Fondos PG offbox copy'; Days = 1; At = '04:30';
       Args = "-d Ubuntu -u root -- bash $repoWsl/backup_offbox.sh --no-dump --mirror-prune" }
)

foreach ($t in $tasks) {
    $line = "{0,-30} every {1} day(s) at {2}: wsl.exe {3}" -f $t.Name, $t.Days, $t.At, $t.Args
    if ($Unregister) {
        if (Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false
            Write-Host "removed: $($t.Name)"
        } else { Write-Host "not registered: $($t.Name)" }
    }
    elseif ($Register) {
        $action   = New-ScheduledTaskAction -Execute 'wsl.exe' -Argument $t.Args
        $trigger  = New-ScheduledTaskTrigger -Daily -DaysInterval $t.Days -At $t.At
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
                        -ExecutionTimeLimit (New-TimeSpan -Hours 3) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $t.Name -Action $action -Trigger $trigger -Settings $settings `
            -Principal $principal -Description 'FND-0179/FND-0290 Postgres backups: base+WAL, logical dump, off-box copy' -Force | Out-Null
        Write-Host "registered: $line"
    }
    else { Write-Host "would register: $line" }
}
if (-not $Register -and -not $Unregister) { Write-Host "`nDry run only. Re-run with -Register to create the tasks, -Unregister to remove them." }
