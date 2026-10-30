<#
 FND-0353, Step 2. ONE elevated run that creates, in this order and atomically (any failure removes what this run created and leaves the downloaded key where it was):
   the isolated account fondos_ci_runner (not an administrator; no interactive, Remote Desktop or network logon; allowed only to log on as a batch job),
   the tree C:\ProgramData\fondos\ci_runner\ with every ACL of doc/operativos/CICD_SERVICE_ACCOUNT_SPEC.md sections 4 and 5, the audit policy and the SACL on the key directory,
   and the App's private key moved into github_app_keys\ (validated as one unmodified RSA PEM, the downloaded copy overwritten and deleted last).
 Run it IN MEMORY from the pushed commit, after checking the blob hash (doc/operativos/PROVISIONING_RUNBOOK.md), so the file in the working tree is never executed:
   & ([scriptblock]::Create($text)) -KeySource <path of the .pem GitHub downloaded>
 It never prints the key or the password. It does NOT deploy open_pr.py or create the scheduled tasks: that code does not exist yet (spec section 11, steps 4 to 6).
#>
param(
    [Parameter(Mandatory)][string]$KeySource
)
$ErrorActionPreference = 'Stop'
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run this in an elevated PowerShell (Run as administrator).' }
$acct   = 'fondos_ci_runner'
$me     = "$env:COMPUTERNAME\$acct"
$parent = 'C:\ProgramData\fondos'
$root   = Join-Path $parent 'ci_runner'
$keyDir = Join-Path $root 'github_app_keys'
$keyDst = Join-Path $keyDir 'fondos-pr-opener.pem'
$tmp    = Join-Path $root '_tmp'
$sys32  = Join-Path $env:SystemRoot 'System32'
$sid = @{ System = 'S-1-5-18'; Everyone = 'S-1-1-0'; Admins = 'S-1-5-32-544'; Users = 'S-1-5-32-545'; Rdp = 'S-1-5-32-555' }
$sidOf = @{}; $sid.Keys | ForEach-Object { $sidOf[$_] = New-Object Security.Principal.SecurityIdentifier($sid[$_]) }
$rightsToSet = @{ SeBatchLogonRight = 'add'; SeDenyInteractiveLogonRight = 'add'; SeDenyRemoteInteractiveLogonRight = 'add'; SeDenyNetworkLogonRight = 'add' }
$createdAccount = $false; $createdRoot = $false; $createdUserList = $false; $originalRights = $null; $runnerSid = $null

function Invoke-Native {
    param([string]$Exe, [string[]]$ArgList, [int[]]$Ok = @(0))
    & $Exe @ArgList | Out-Null
    if ($Ok -notcontains $LASTEXITCODE) { throw "$Exe failed with exit code $LASTEXITCODE" }
}
function Set-Icacls { param([string]$Path, [string[]]$Rest) Invoke-Native 'icacls.exe' (@($Path) + $Rest) }

function Test-AppKey {
    # $null when the text is exactly one unmodified RSA private key PEM of about 2048 bits, otherwise the reason
    param([string]$Text)
    $t = $Text -replace "`r", ''
    if ($t -notmatch '(?s)\A-----BEGIN RSA PRIVATE KEY-----\n([A-Za-z0-9+/=\n]+)\n-----END RSA PRIVATE KEY-----\n?\z') { return 'not exactly one unmodified RSA PRIVATE KEY PEM' }
    $b64 = $Matches[1] -replace "`n", ''
    try { $der = [Convert]::FromBase64String($b64) } catch { return 'the base64 body is invalid' }
    if ($der.Length -lt 1150 -or $der.Length -gt 1250) { return "the key body is $($der.Length) bytes: not a 2048-bit RSA key" }
    return $null
}
function Get-UserRights {
    param([string]$Cfg)
    Invoke-Native (Join-Path $sys32 'secedit.exe') @('/export', '/cfg', $Cfg, '/areas', 'USER_RIGHTS', '/quiet')
    $map = @{}
    foreach ($line in (Get-Content -Path $Cfg -Encoding Unicode)) {
        if ($line -match '^(Se\w+)\s*=\s*(.*)$') { $map[$Matches[1]] = @($Matches[2] -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }) }
    }
    return $map
}
function Set-UserRight {
    param([string]$Right, [string[]]$List)
    $inf = Join-Path $tmp 'rights.inf'
    Set-Content -Path $inf -Encoding Unicode -Value @('[Unicode]', 'Unicode=yes', '[Version]', 'signature="$CHICAGO$"', 'Revision=1', '[Privilege Rights]', "$Right = $($List -join ',')")
    Invoke-Native (Join-Path $sys32 'secedit.exe') @('/configure', '/db', (Join-Path $tmp 'rights.sdb'), '/cfg', $inf, '/areas', 'USER_RIGHTS', '/quiet')
}

try {
    # --- 0. Preconditions ---------------------------------------------------------------------------------------------------------------
    if (Test-Path $root) { throw "$root already exists: stop and review it by hand (remove it only after reading it)." }
    if (Get-LocalUser -Name $acct -ErrorAction SilentlyContinue) { throw "$acct already exists: stop and review it by hand." }
    if (-not (Test-Path -LiteralPath $KeySource -PathType Leaf)) { throw "KeySource not found: $KeySource" }
    if ((Get-Item -LiteralPath $KeySource).Length -gt 8192) { throw 'KeySource is larger than 8 KB: not an App key.' }
    $keyText = [IO.File]::ReadAllText($KeySource)
    $why = Test-AppKey $keyText
    $keyText = $null
    if ($why) { throw "KeySource rejected: $why." }

    # --- 1. The daily user, from the logged-on session ------------------------------------------------------------------------------------
    $console = (Get-CimInstance Win32_ComputerSystem).UserName
    $daily = if ($console) { $console } else { "$env:COMPUTERNAME\$env:USERNAME" }
    $typed = Read-Host "Daily user (inbox write, outbox read) [$daily] (Enter accepts, or type DOMAIN\user)"
    if ($typed) { $daily = $typed }
    $dailySid = (New-Object Security.Principal.NTAccount($daily)).Translate([Security.Principal.SecurityIdentifier])
    if ($dailySid.Value -in @($sid.System, $sid.Admins, $sid.Everyone, $sid.Users)) { throw "$daily is a built-in principal, not a user." }
    Write-Host "Daily user: $daily ($($dailySid.Value))"

    # --- 2. UAC on the secure desktop -----------------------------------------------------------------------------------------------------
    $pol = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'
    if ((Get-ItemProperty $pol).EnableLUA -ne 1) { throw 'EnableLUA is not 1: UAC is off. Turn it on and restart before continuing.' }
    New-ItemProperty -Path $pol -Name ConsentPromptBehaviorAdmin -Value 1 -PropertyType DWord -Force | Out-Null
    New-ItemProperty -Path $pol -Name PromptOnSecureDesktop -Value 1 -PropertyType DWord -Force | Out-Null

    # --- 3. The root, locked before anything else is created in it ------------------------------------------------------------------------
    if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent | Out-Null }
    if ((Get-Acl $parent).GetOwner([Security.Principal.SecurityIdentifier]) -eq $dailySid) { throw "${parent} is owned by ${daily}: an agent could swap what is inside. Delete it (after checking it) and run again." }
    New-Item -ItemType Directory -Path $root | Out-Null; $createdRoot = $true
    Set-Icacls $root @('/inheritance:r', '/grant:r', "*$($sid.Admins):(OI)(CI)(F)", "*$($sid.System):(OI)(CI)(F)")
    New-Item -ItemType Directory -Path $tmp | Out-Null

    # --- 4. The account and its logon rights ----------------------------------------------------------------------------------------------
    $pw1 = Read-Host "Password for $acct" -AsSecureString
    $pw2 = Read-Host 'Repeat the password' -AsSecureString
    if ([Net.NetworkCredential]::new('', $pw1).Password -cne [Net.NetworkCredential]::new('', $pw2).Password) { throw 'The two passwords differ.' }
    New-LocalUser -Name $acct -Password $pw1 -PasswordNeverExpires -UserMayNotChangePassword -AccountNeverExpires -Description 'Runs open_pr.py (GitHub App) from a scheduled task. Not an administrator, no interactive logon.' | Out-Null
    $createdAccount = $true
    $runnerSid = (Get-LocalUser -Name $acct).SID
    Add-LocalGroupMember -SID $sidOf.Users -Member $acct
    $ul = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\SpecialAccounts\UserList'
    New-Item -Path $ul -Force | Out-Null
    New-ItemProperty -Path $ul -Name $acct -Value 0 -PropertyType DWord -Force | Out-Null; $createdUserList = $true
    $originalRights = Get-UserRights (Join-Path $tmp 'rights_before.cfg')
    foreach ($r in $rightsToSet.Keys) {
        $cur = if ($originalRights.ContainsKey($r)) { @($originalRights[$r]) } else { @() }
        Set-UserRight $r ($cur + "*$($runnerSid.Value)")
    }

    # --- 5. The tree and the ACLs of spec sections 4 and 5 ---------------------------------------------------------------------------------
    foreach ($d in 'github_app_keys', 'app', 'venv', 'inbox', 'outbox', 'quarantine', 'alerts') { New-Item -ItemType Directory -Path (Join-Path $root $d) | Out-Null }
    Set-Icacls $root @('/inheritance:r', '/grant:r', "*$($sid.Admins):(OI)(CI)(F)", "*$($sid.System):(OI)(CI)(F)", "${me}:(RX)", "*$($dailySid.Value):(RX)")
    # key directory: exactly three principals, the daily user is NOT one of them
    Invoke-Native 'auditpol.exe' @('/set', '/subcategory:{0CCE921D-69AE-11D9-BED3-505054503030}', '/success:enable', '/failure:enable')
    $audRights = [Security.AccessControl.FileSystemRights]'ReadData, WriteData, Delete, ChangePermissions, TakeOwnership'
    $acl = Get-Acl -Path $keyDir -Audit
    $acl.AddAuditRule((New-Object Security.AccessControl.FileSystemAuditRule($sidOf.Everyone, $audRights, 'ContainerInherit, ObjectInherit', 'None', 'Success, Failure')))
    Set-Acl -Path $keyDir -AclObject $acl
    Set-Icacls $keyDir @('/inheritance:r', '/grant:r', "${me}:(OI)(CI)(R)", "*$($sid.System):(OI)(CI)(F)", "*$($sid.Admins):(OI)(CI)(F)")
    foreach ($d in 'app', 'venv') {
        Set-Icacls (Join-Path $root $d) @('/inheritance:r', '/grant:r', "${me}:(OI)(CI)(RX)", "*$($sid.Admins):(OI)(CI)(F)", "*$($sid.System):(OI)(CI)(RX)", "*$($dailySid.Value):(OI)(CI)(RX)")
    }
    Set-Icacls (Join-Path $root 'inbox') @('/inheritance:r', '/grant:r', "${me}:(OI)(CI)(M)", "*$($dailySid.Value):(OI)(CI)(M)", "*$($sid.Admins):(OI)(CI)(F)")
    foreach ($d in 'outbox', 'quarantine', 'alerts') {
        Set-Icacls (Join-Path $root $d) @('/inheritance:r', '/grant:r', "${me}:(OI)(CI)(M)", "*$($dailySid.Value):(OI)(CI)(R)", "*$($sid.Admins):(OI)(CI)(F)")
    }

    # --- 6. The key: copied into the locked directory (audited), checked, and only then the downloaded copy is overwritten and deleted -----
    Copy-Item -LiteralPath $KeySource -Destination $keyDst
    $why = Test-AppKey ([IO.File]::ReadAllText($keyDst))
    if ($why) { throw "the copy in $keyDir is not valid: $why." }

    # --- 7. Assertions: any failure undoes the whole run (the downloaded key is still untouched) -------------------------------------------
    $a = Get-Acl -Path $keyDir
    $rules = @($a.Access)
    if (-not $a.AreAccessRulesProtected) { throw 'ASSERT: the key directory inherits from its parent.' }
    if ($rules.Count -ne 3 -or @($rules | Where-Object { $_.IsInherited -or $_.AccessControlType -ne 'Allow' }).Count -ne 0) { throw 'ASSERT: the key directory must hold exactly three own allow entries.' }
    $who = @($rules | ForEach-Object { $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value })
    foreach ($want in @($runnerSid.Value, $sid.System, $sid.Admins)) { if ($who -notcontains $want) { throw "ASSERT: $want is missing from the key directory." } }
    if ($who -contains $dailySid.Value) { throw 'ASSERT: the daily user is on the key directory ACL.' }
    $runnerRule = @($rules | Where-Object { $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]) -eq $runnerSid })[0]
    if ($runnerRule.FileSystemRights -band ([Security.AccessControl.FileSystemRights]'WriteData, AppendData, Delete, ChangePermissions, TakeOwnership')) { throw 'ASSERT: the runner can write the key directory.' }
    foreach ($d in 'app', 'venv') {
        foreach ($r in @((Get-Acl (Join-Path $root $d)).Access | Where-Object { $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]) -in @($dailySid, $runnerSid) })) {
            if ($r.FileSystemRights -band ([Security.AccessControl.FileSystemRights]'WriteData, AppendData, Delete, ChangePermissions, TakeOwnership')) { throw "ASSERT: a non-administrator can write $d." }
        }
    }
    foreach ($d in 'outbox', 'quarantine', 'alerts') {
        $dr = @((Get-Acl (Join-Path $root $d)).Access | Where-Object { $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]) -eq $dailySid })
        if ($dr.Count -ne 1 -or ($dr[0].FileSystemRights -band ([Security.AccessControl.FileSystemRights]'WriteData, AppendData, Delete'))) { throw "ASSERT: the daily user can write $d (a forged receipt would be possible)." }
    }
    if (@(Get-LocalGroupMember -SID $sidOf.Admins | ForEach-Object { $_.Name }) -contains $me) { throw 'ASSERT: fondos_ci_runner is an administrator.' }
    if (@(Get-LocalGroupMember -SID $sidOf.Rdp | ForEach-Object { $_.Name }) -contains $me) { throw 'ASSERT: fondos_ci_runner can use Remote Desktop.' }
    $now = Get-UserRights (Join-Path $tmp 'rights_after.cfg')
    foreach ($r in $rightsToSet.Keys) { if ($now[$r] -notcontains "*$($runnerSid.Value)") { throw "ASSERT: $r does not include fondos_ci_runner." } }
    $pol2 = Get-ItemProperty $pol
    if ($pol2.ConsentPromptBehaviorAdmin -ne 1 -or $pol2.PromptOnSecureDesktop -ne 1) { throw 'ASSERT: UAC is not on the secure-desktop credential prompt.' }

    # --- 8. Last: overwrite and delete the downloaded copy (best effort on an SSD: a rotation in GitHub is the real remedy) ----------------
    $len = (Get-Item -LiteralPath $KeySource).Length
    [IO.File]::WriteAllBytes($KeySource, (New-Object byte[] $len))
    Remove-Item -LiteralPath $KeySource -Force
    Remove-Item -LiteralPath $tmp -Recurse -Force
    Write-Host "PROVISIONED OK. Account $me; key in $keyDir (not printed). The downloaded key file was overwritten and deleted."
    Write-Host 'Next: install the App on the repository (CICD spec section 13, steps 14 to 16). open_pr.py and the scheduled tasks are not part of this run.'
    icacls $keyDir
}
catch {
    $err = $_
    Write-Host "FAILED: $($err.Exception.Message). Undoing what this run created; the downloaded key was not touched." -ForegroundColor Red
    try {
        if ($originalRights) {
            foreach ($r in $rightsToSet.Keys) {
                $orig = if ($originalRights.ContainsKey($r)) { @($originalRights[$r]) } else { @() }
                Set-UserRight $r $orig
            }
        }
        if ($createdUserList) { Remove-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\SpecialAccounts\UserList' -Name $acct -ErrorAction SilentlyContinue }
        if ($createdAccount) { Remove-LocalUser -Name $acct -ErrorAction SilentlyContinue }
        if ($createdRoot -and (Test-Path $root)) {
            Invoke-Native 'takeown.exe' @('/F', $root, '/R', '/D', 'Y') -Ok (0..1)
            Invoke-Native 'icacls.exe' @($root, '/grant', "*$($sid.Admins):(OI)(CI)(F)", '/T', '/C', '/Q') -Ok (0..1)
            Remove-Item $root -Recurse -Force -ErrorAction SilentlyContinue
        }
    } catch { Write-Host "Cleanup incomplete: $($_.Exception.Message). Review $root, the account $acct and the user rights by hand." -ForegroundColor Red }
    $global:LASTEXITCODE = 1
    return
}
