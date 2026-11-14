# Registers the recurring Postgres backup tasks in Windows Task Scheduler (FND-0179 / FND-0073 / FND-0290 / FND-0407).
#
#   powershell -ExecutionPolicy Bypass -File scripts\ops\register_backup_tasks.ps1              # show what WOULD be registered
#   powershell -ExecutionPolicy Bypass -File scripts\ops\register_backup_tasks.ps1 -Register     # register the three tasks (re-registers with -Force)
#   powershell -ExecutionPolicy Bypass -File scripts\ops\register_backup_tasks.ps1 -Unregister   # remove them
#
# PRIORITY RULE (owner order 2026-11-14): a RUNNING WORK CYCLE ALWAYS WINS. Every task below ends in a script that reads the cycle lock first
# (scripts/ops/lib/cycle_guard.sh) and, while a cycle runs, exits 75 = DEFERRED without touching the database. Each task therefore repeats HOURLY after its
# first trigger, so a backup deferred by a night-long cycle runs by itself in the first free hour: no manual step. A script that finds nothing to do exits at
# once (a set of today exists / the newest base is younger than 3 days), so the repetition costs nothing. The backup process itself runs at idle I/O class and the
# lowest CPU priority. (On 2026-11-14 the 01:00 base-backup task ran inside a cycle and a Postgres server process died: FND-0407.)
#
# Policy (FND-0179, owner-approved 2026-10-02): a physical base backup every 3 days, keep the newest 2 bases, prune the WAL archive older than the oldest kept
# base right after a verified base, then mirror to the external disk D: daily. At ~18 GB/day of WAL this keeps <= ~6 days of WAL + 2 bases (~190 GB) on each side.
# Logical dumps (owner order 2026-11-14): one set per day, ONLY THE TWO NEWEST DATES kept, on the computer and on D:.
#
#   Task 1  "Fondos PG backup (rotating)"  daily 02:00, repeats hourly for 22 h
#           backup_rotating.sh   (cycle guard, pg_dump -Fc of fondos + gestion + roles into /mnt/backups/pg_fondos/dump, rotation to the 2 newest dates,
#           SHA-256-verified copy to D: and the same rotation there). Independent of the external disk: D: absent ends the run LOCAL_ONLY (exit 1) and the next
#           run copies the set when D: is back. Replaces the old "Fondos PG logical dump" task (backup_live_pg.sh directly, keep 7), removed on -Register.
#   Task 2  "Fondos PG basebackup+prune"   daily 02:30, repeats hourly for 8 h
#           setup_wal_archive.sh basebackup --if-due=3 && setup_wal_archive.sh prune --yes   (runs as root in WSL)
#           --if-due=3 takes a base only when the newest complete one is at least 3 days old; prune only runs if that succeeded (&&) and deletes nothing until
#           2 bases exist.
#   Task 3  "Fondos PG offbox copy"        daily 04:30, repeats hourly for 8 h
#           backup_offbox.sh --no-dump --mirror-prune   (PITR rsync to D: and the guarded mirror of the source's pruning; it takes no dump of its own).
#
# They run as the current user while logged on (same as the existing WSL keepalive task) and catch up after a missed start. Nothing here touches the
# database; the tasks do, when they fire.
param([switch]$Register, [switch]$Unregister)

$ErrorActionPreference = 'Stop'
$repoWsl = '/mnt/c/desarrollo/fondos/scripts/ops'
$legacy = @('Fondos PG logical dump')
$tasks = @(
    @{ Name = 'Fondos PG backup (rotating)'; Days = 1; At = '02:00'; RepeatMin = 60; ForHours = 22;
       Args = "-d Ubuntu -u root -- bash $repoWsl/backup_rotating.sh" },
    @{ Name = 'Fondos PG basebackup+prune'; Days = 1; At = '02:30'; RepeatMin = 60; ForHours = 8;
       Args = "-d Ubuntu -u root -- bash -c ""bash $repoWsl/setup_wal_archive.sh basebackup --if-due=3 && bash $repoWsl/setup_wal_archive.sh prune --yes""" },
    @{ Name = 'Fondos PG offbox copy'; Days = 1; At = '04:30'; RepeatMin = 60; ForHours = 8;
       Args = "-d Ubuntu -u root -- bash $repoWsl/backup_offbox.sh --no-dump --mirror-prune" }
)

if ($Register) {
    foreach ($name in $legacy) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Host "removed (superseded): $name"
        }
    }
}

foreach ($t in $tasks) {
    $line = "{0,-30} daily at {1}, repeats every {2} min for {3} h: wsl.exe {4}" -f $t.Name, $t.At, $t.RepeatMin, $t.ForHours, $t.Args
    if ($Unregister) {
        foreach ($name in @($t.Name) + $legacy) {
            if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
                Unregister-ScheduledTask -TaskName $name -Confirm:$false
                Write-Host "removed: $name"
            }
        }
    }
    elseif ($Register) {
        $action   = New-ScheduledTaskAction -Execute 'wsl.exe' -Argument $t.Args
        $trigger  = New-ScheduledTaskTrigger -Daily -DaysInterval $t.Days -At $t.At
        $repeat   = New-ScheduledTaskTrigger -Once -At $t.At -RepetitionInterval (New-TimeSpan -Minutes $t.RepeatMin) -RepetitionDuration (New-TimeSpan -Hours $t.ForHours)
        $trigger.Repetition = $repeat.Repetition
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
                        -ExecutionTimeLimit (New-TimeSpan -Hours 3) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $t.Name -Action $action -Trigger $trigger -Settings $settings `
            -Principal $principal -Description 'FND-0179/FND-0290/FND-0407 Postgres backups: cycle-aware, hourly retry' -Force | Out-Null
        Write-Host "registered: $line"
    }
    else { Write-Host "would register: $line" }
}
if (-not $Register -and -not $Unregister) { Write-Host "`nDry run only. Re-run with -Register to create the tasks, -Unregister to remove them." }
