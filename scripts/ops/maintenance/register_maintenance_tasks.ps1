# Registers the preventive-maintenance cadence in Windows Task Scheduler (same pattern as register_backup_tasks.ps1).
#
#   powershell -ExecutionPolicy Bypass -File scripts\ops\maintenance\register_maintenance_tasks.ps1              # dry run: show what WOULD be registered
#   powershell -ExecutionPolicy Bypass -File scripts\ops\maintenance\register_maintenance_tasks.ps1 -Register     # register
#   powershell -ExecutionPolicy Bypass -File scripts\ops\maintenance\register_maintenance_tasks.ps1 -Unregister   # remove
#
# Cadence (times avoid the 02:00 base backup and 04:30 off-box copy of register_backup_tasks.ps1):
#   daily   06:30  pg_run_all.sh daily  + wsl_run_all.sh daily            (read-only)
#   weekly  Sun 07:00  pg_run_all.sh weekly --apply + wsl_run_all.sh weekly --apply   (vacuum/analyze, docker/log cleanup, OS patches w/o docker)
#   monthly 1st 07:00  pg_run_all.sh monthly (read-only reindex report) ; wsl_05_host_vhdx.ps1 report
# --apply is deliberately NOT used for the monthly -verify-restore / VHDX compaction: run those by hand.
# Results land in C:\data\logs\maintenance\*.log; a CRITICAL exit makes the task show a non-zero last result.
param([switch]$Register, [switch]$Unregister)
$ErrorActionPreference = 'Stop'
$m = '/mnt/c/desarrollo/fondos/scripts/ops/maintenance'
$ps = 'C:\desarrollo\fondos\scripts\ops\maintenance\wsl\wsl_05_host_vhdx.ps1'
$tasks = @(
  @{ Name = 'Fondos maint daily';   Trigger = { New-ScheduledTaskTrigger -Daily -At 06:30 };
     Exe = 'wsl.exe'; Args = "-d Ubuntu -u root -- bash -c ""bash $m/pg/pg_run_all.sh daily; r1=`$?; bash $m/wsl/wsl_run_all.sh daily; r2=`$?; exit `$((r1>r2?r1:r2))""" },
  @{ Name = 'Fondos maint weekly';  Trigger = { New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 07:00 };
     Exe = 'wsl.exe'; Args = "-d Ubuntu -u root -- bash -c ""bash $m/pg/pg_run_all.sh weekly --apply; r1=`$?; bash $m/wsl/wsl_run_all.sh weekly --apply; r2=`$?; exit `$((r1>r2?r1:r2))""" },
  @{ Name = 'Fondos maint monthly'; Trigger = { New-ScheduledTaskTrigger -Daily -At 07:30 };   # fires daily, script gate below limits it to day 1
     Exe = 'powershell.exe'; Args = "-NoProfile -ExecutionPolicy Bypass -Command ""if ((Get-Date).Day -eq 1) { wsl.exe -d Ubuntu -u root -- bash $m/pg/pg_run_all.sh monthly; & '$ps' }""" }
)
foreach ($t in $tasks) {
  $line = "{0,-22} {1} {2}" -f $t.Name, $t.Exe, $t.Args
  if ($Unregister) {
    if (Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false; Write-Host "removed: $($t.Name)" } else { Write-Host "not registered: $($t.Name)" }
  } elseif ($Register) {
    $action = New-ScheduledTaskAction -Execute $t.Exe -Argument $t.Args
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 3) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $t.Name -Action $action -Trigger (& $t.Trigger) -Settings $settings -Principal $principal -Description 'Preventive maintenance (Postgres + WSL)' -Force | Out-Null
    Write-Host "registered: $line"
  } else { Write-Host "would register: $line" }
}
if (-not $Register -and -not $Unregister) { Write-Host "`nDry run only. Re-run with -Register to create the tasks, -Unregister to remove them." }
