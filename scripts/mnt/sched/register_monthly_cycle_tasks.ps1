# Registers the two scheduled monthly cycle tasks in Windows Task Scheduler (FND-0447, FND-0448).
#
#   powershell -ExecutionPolicy Bypass -File scripts\mnt\sched\register_monthly_cycle_tasks.ps1              # show what WOULD be registered
#   powershell -ExecutionPolicy Bypass -File scripts\mnt\sched\register_monthly_cycle_tasks.ps1 -Register    # register both tasks (re-registers with -Force)
#   powershell -ExecutionPolicy Bypass -File scripts\mnt\sched\register_monthly_cycle_tasks.ps1 -Unregister  # remove both
#
# OWNER STANDING ORDERS (2026-11-26):
#   * scheduled monthly P3 builds are authorised strictly in DRAFT-only mode: they generate the DRAFT and the shadow rows and never promote to PRODUCTION without
#     the owner's explicit order;
#   * the order is extended EXCLUSIVELY to the P1/P2 ingestion and calculation cycle (the monthly NAV refresh), so the build does not fail on stale data.
# Promotions, switch flips, KIID sync / retire and every other run still need his order. Neither launcher has such a step (tests/test_p3_monthly_draft.py,
# tests/test_p1p2_monthly_refresh.py).
#
#   Task  "FondosP1P2MonthlyRefresh"  daily at 20:00   scripts\launch\P1P2_monthlyRefresh.bat
#         From day 25 to the end of the month, runs P1P2_runCycle.bat (harvest read-only) while the NAV or the harvest would fail the P3 freshness gate on the
#         last retry day of the next build; once fresh enough, it does nothing until the next window.
#   Task  "FondosP3MonthlyDraft"      daily at 09:00   scripts\launch\P3_monthlyDraft.bat
#         Builds the DRAFT through P3_runCycle.bat while the current calendar month (UTC) has no DRAFT or no row of a shadow stream that is ON: it builds on day 1
#         and does nothing for the rest of the month.
# A failed day is retried by each task the next day. A running work cycle always wins: both launchers share the single-instance lock and exit 105.
#
# They run as the current user while logged on (like the WSL keepalive and the backup tasks, so the WSL distro with the live Postgres is up) and catch up after a
# missed start. Nothing here touches the database; the tasks do, when they fire. The repository path is derived from this file's location: re-register after
# moving the repository.
param([switch]$Register, [switch]$Unregister)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$launch = Join-Path $root 'scripts\launch'
$tasks = @(
    @{ Name = 'FondosP1P2MonthlyRefresh'; Bat = 'P1P2_monthlyRefresh.bat'; At = '20:00'; Hours = 8;
       Desc = 'FND-0448: monthly P1+P2 cycle (NAV refresh) from day 25, owner standing order 2026-11-26 (ingestion and calculation only)' },
    @{ Name = 'FondosP3MonthlyDraft'; Bat = 'P3_monthlyDraft.bat'; At = '09:00'; Hours = 6;
       Desc = 'FND-0447: monthly P3 build, DRAFT-only (owner standing order 2026-11-26); never promotes' }
)

foreach ($t in $tasks) {
    $bat = Join-Path $launch $t.Bat
    if (-not (Test-Path $bat)) { throw "launcher not found: $bat" }
    $line = "{0,-26} daily at {1}: cmd.exe /c `"{2}`"" -f $t.Name, $t.At, $bat
    if ($Unregister) {
        if (Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false
            Write-Host "removed: $($t.Name)"
        } else { Write-Host "not registered: $($t.Name)" }
    }
    elseif ($Register) {
        $action   = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"$bat`"" -WorkingDirectory $launch
        $trigger  = New-ScheduledTaskTrigger -Daily -At $t.At
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
                        -ExecutionTimeLimit (New-TimeSpan -Hours $t.Hours) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $t.Name -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
            -Description $t.Desc -Force | Out-Null
        Write-Host "registered: $line"
    }
    else { Write-Host "would register: $line" }
}
if (-not $Register -and -not $Unregister) { Write-Host "`nDry run only. Re-run with -Register to create the tasks, -Unregister to remove them." }
