# Registers the scheduled monthly P3 build, DRAFT-only, in Windows Task Scheduler (FND-0447).
#
#   powershell -ExecutionPolicy Bypass -File scripts\mnt\sched\register_p3_monthly_draft.ps1              # show what WOULD be registered
#   powershell -ExecutionPolicy Bypass -File scripts\mnt\sched\register_p3_monthly_draft.ps1 -Register    # register the task (re-registers with -Force)
#   powershell -ExecutionPolicy Bypass -File scripts\mnt\sched\register_p3_monthly_draft.ps1 -Unregister  # remove it
#   ... -At 09:00                                                                                         # the daily start time (default 09:00)
#
# OWNER STANDING ORDER (2026-11-26): scheduled monthly builds are authorised strictly in DRAFT-only mode. They generate the DRAFT and the shadow rows and
# never promote to PRODUCTION without the owner's explicit order. The task runs scripts\launch\P3_monthlyDraft.bat, which builds through P3_runCycle.bat
# (DRAFT, the P2 audit after it, the DRAFT report) and has no promotion step (tests/test_p3_monthly_draft.py).
#
#   Task  "FondosP3MonthlyDraft"  daily at -At, catches up after a missed start
#         The launcher builds only while the current calendar month (UTC) has no DRAFT or no row of a shadow stream that is ON, so the task can fire every day:
#         it builds on the first day of the month and does nothing for the rest of it. A failed day (stale P3 inputs, another cycle holding the lock, the database
#         down) is retried by itself the next day. A running work cycle always wins: the launcher shares the single-instance lock and exits 105.
#
# It runs as the current user while logged on (like the WSL keepalive and the backup tasks, so the WSL distro with the live Postgres is up). Nothing here
# touches the database; the task does, when it fires. The repository path is derived from this file's location, so re-register after moving the repository.
param([switch]$Register, [switch]$Unregister, [string]$At = '09:00')

$ErrorActionPreference = 'Stop'
$name = 'FondosP3MonthlyDraft'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$launch = Join-Path $root 'scripts\launch'
$bat = Join-Path $launch 'P3_monthlyDraft.bat'
if (-not (Test-Path $bat)) { throw "launcher not found: $bat" }
$line = "{0} daily at {1}: cmd.exe /c `"{2}`" (working directory {3})" -f $name, $At, $bat, $launch

if ($Unregister) {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "removed: $name"
    } else { Write-Host "not registered: $name" }
}
elseif ($Register) {
    $action   = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"$bat`"" -WorkingDirectory $launch
    $trigger  = New-ScheduledTaskTrigger -Daily -At $At
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
                    -ExecutionTimeLimit (New-TimeSpan -Hours 6) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
        -Description 'FND-0447: monthly P3 build, DRAFT-only (owner standing order 2026-11-26); never promotes' -Force | Out-Null
    Write-Host "registered: $line"
}
else {
    Write-Host "would register: $line"
    Write-Host "`nDry run only. Re-run with -Register to create the task, -Unregister to remove it."
}
