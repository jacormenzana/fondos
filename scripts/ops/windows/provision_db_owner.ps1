#Requires -RunAsAdministrator
<#
 FND-0354, Step 3. ONE elevated run that creates, in this order and atomically (any failure removes what this run created):
   the isolated account fondos_db_owner, the vault with the owner DSN, the read-only tool tree (app\ + runtime\) extracted from ONE pushed commit,
   MANIFEST.sha256, every ACL, the audit rules, and the final assertions.
 Run it IN MEMORY from the pushed commit it deploys, after checking the blob hash (doc/operativos/OWNER_DSN_VAULT.md section 3), so the file in the working tree is never executed:
   & ([scriptblock]::Create($text)) -Commit <40-hex SHA>
 It never prints the DSN, writes it to a command line, or leaves it in a variable after use.
#>
param(
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{40}$')][string]$Commit,
    [string]$Repo = 'C:\desarrollo\fondos',
    [string]$PythonHome = 'C:\data\envs\des'
)
$ErrorActionPreference = 'Stop'
$Commit = $Commit.ToLowerInvariant()
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run this in an elevated PowerShell (Run as administrator).' }
$acct  = 'fondos_db_owner'
$me    = "$env:COMPUTERNAME\$acct"
$parent = 'C:\ProgramData\fondos'
$root  = Join-Path $parent 'db_owner'
$vault = Join-Path $root 'vault'
$file  = Join-Path $vault 'owner_dsn.conf'
$sys32 = Join-Path $env:SystemRoot 'System32'
$tar   = Join-Path $sys32 'tar.exe'
$robo  = Join-Path $sys32 'robocopy.exe'
$sid = @{ System = 'S-1-5-18'; Everyone = 'S-1-1-0'; Admins = 'S-1-5-32-544'; Users = 'S-1-5-32-545'; Rdp = 'S-1-5-32-555' }
$sidOf = @{}; $sid.Keys | ForEach-Object { $sidOf[$_] = New-Object Security.Principal.SecurityIdentifier($sid[$_]) }
$payload = @('shared', 'db/pg_backlog', 'scripts/ops/migrate_backlog_links.py', 'scripts/ops/migrate_backlog_v2.py',
             'scripts/ops/db_owner_preflight.py', 'scripts/ops/db_owner_manifest.py', 'scripts/ops/windows/run_db_owner.cmd')
$createdAccount = $false; $createdRoot = $false; $createdUserList = $false

function Invoke-Native {
    param([string]$Exe, [string[]]$ArgList, [int[]]$Ok = @(0))
    & $Exe @ArgList | Out-Null
    if ($Ok -notcontains $LASTEXITCODE) { throw "$Exe failed with exit code $LASTEXITCODE" }
}
function Set-Icacls { param([string]$Path, [string[]]$Rest) Invoke-Native 'icacls.exe' (@($Path) + $Rest) }

function Get-RecordedHashes {
    # {lowercase relative path with '/': sha256 hex} from the install-time records of the environment: conda-meta (sha256_in_prefix when the file was relocated) and the RECORD of the pip packages we copy
    param([string]$EnvDir)
    Add-Type -AssemblyName System.Web.Extensions
    $js = New-Object System.Web.Script.Serialization.JavaScriptSerializer
    $js.MaxJsonLength = [int]::MaxValue
    $js.RecursionLimit = 200
    $map = @{}
    foreach ($f in (Get-ChildItem -LiteralPath (Join-Path $EnvDir 'conda-meta') -Filter '*.json' -File)) {
        $d = $js.DeserializeObject([IO.File]::ReadAllText($f.FullName))
        if (-not $d.ContainsKey('paths_data')) { continue }
        foreach ($p in $d['paths_data']['paths']) {
            $h = if ($p.ContainsKey('sha256_in_prefix')) { $p['sha256_in_prefix'] } elseif ($p.ContainsKey('sha256')) { $p['sha256'] } else { $null }
            if ($h) { $map[$p['_path'].Replace('\', '/').ToLowerInvariant()] = $h.ToLowerInvariant() }
        }
    }
    $sp = Join-Path $EnvDir 'Lib\site-packages'
    foreach ($pkg in 'psycopg', 'psycopg_binary', 'typing_extensions', 'tzdata') {
        foreach ($dist in (Get-ChildItem -LiteralPath $sp -Directory -Filter "$pkg-*.dist-info")) {
            foreach ($line in [IO.File]::ReadAllLines((Join-Path $dist.FullName 'RECORD'))) {
                $c = $line.Split(',')
                if ($c.Length -lt 3 -or -not $c[$c.Length - 2].StartsWith('sha256=')) { continue }
                $b64 = $c[$c.Length - 2].Substring(7).Replace('-', '+').Replace('_', '/')
                $b64 = $b64.PadRight($b64.Length + ((4 - $b64.Length % 4) % 4), '=')
                $name = ($c[0..($c.Length - 3)] -join ',').Replace('\', '/')
                $map[('lib/site-packages/' + $name).ToLowerInvariant()] = ([BitConverter]::ToString([Convert]::FromBase64String($b64))).Replace('-', '').ToLowerInvariant()
            }
        }
    }
    return $map
}
function Test-RuntimeIntegrity {
    # Every file of the COPY must (1) match the hash recorded when it was installed and (2) not be bytecode, a .pth pointer or a startup hook. Returns {Verified, Problems}.
    param([string]$EnvDir, [string]$Copy)
    $rec = Get-RecordedHashes $EnvDir
    $problems = New-Object System.Collections.Generic.List[string]
    $verified = 0
    foreach ($f in (Get-ChildItem -LiteralPath $Copy -Recurse -File -Force)) {
        $rel = $f.FullName.Substring($Copy.Length + 1).Replace('\', '/').ToLowerInvariant()
        if ($rel -match '(^|/)__pycache__(/|$)|\.(pyc|pyo|pth|egg-link)$|(^|/)(sitecustomize|usercustomize)\.py$') { $problems.Add("FORBIDDEN $rel"); continue }
        if (-not $rec.ContainsKey($rel)) { $problems.Add("UNRECORDED $rel"); continue }
        if ((Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash.ToLowerInvariant() -ne $rec[$rel]) { $problems.Add("MISMATCH $rel"); continue }
        $verified++
    }
    return [pscustomobject]@{ Verified = $verified; Problems = $problems }
}

try {
    # --- 0. Preconditions -------------------------------------------------------------------------------------------------------------
    $gitExe = (Get-Command git.exe -ErrorAction Stop).Source
    if (-not $gitExe.StartsWith($env:ProgramFiles, [StringComparison]::OrdinalIgnoreCase)) { throw "git.exe resolves to $gitExe, outside Program Files: refusing to run a user-writable binary elevated." }
    if (-not (Test-Path $tar) -or -not (Test-Path $robo)) { throw 'tar.exe or robocopy.exe missing from System32.' }
    $git = @('-c', "safe.directory=$Repo", '-C', $Repo)
    & $gitExe @git cat-file -e "$Commit^{commit}"
    if ($LASTEXITCODE -ne 0) { throw "commit $Commit does not exist in $Repo." }
    $remotes = & $gitExe @git branch -r --contains $Commit
    if (-not $remotes) { throw "commit $Commit is not on any origin branch: only a pushed, reviewable commit is deployed." }
    Write-Host ("Deploying: " + (& $gitExe @git log -1 --format='%h %s' $Commit))
    if (Test-Path $root) { throw "$root already exists: stop and review it by hand (remove it only after reading it)." }
    if (Get-LocalUser -Name $acct -ErrorAction SilentlyContinue) { throw "$acct already exists: stop and review it by hand." }
    if (-not (Test-Path (Join-Path $PythonHome 'python.exe')) -or -not (Test-Path (Join-Path $PythonHome 'Lib\site-packages\psycopg'))) { throw "$PythonHome is not the project environment (python.exe or psycopg missing)." }

    # --- 1. The daily user, resolved from the logged-on session (not from the elevated token, which may be another administrator) ----------
    $console = (Get-CimInstance Win32_ComputerSystem).UserName
    $daily = if ($console) { $console } else { "$env:COMPUTERNAME\$env:USERNAME" }
    $typed = Read-Host "Daily user that gets read and execute on the tool tree [$daily] (Enter accepts, or type DOMAIN\user)"
    if ($typed) { $daily = $typed }
    $dailySid = (New-Object Security.Principal.NTAccount($daily)).Translate([Security.Principal.SecurityIdentifier])
    if ($dailySid.Value -in @($sid.System, $sid.Admins, $sid.Everyone, $sid.Users)) { throw "$daily is a built-in principal, not a user." }
    Write-Host "Daily user: $daily ($($dailySid.Value))"

    # --- 2. UAC on the secure desktop ---------------------------------------------------------------------------------------------------
    $pol = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'
    if ((Get-ItemProperty $pol).EnableLUA -ne 1) { throw 'EnableLUA is not 1: UAC is off. Turn it on and restart before continuing.' }
    New-ItemProperty -Path $pol -Name ConsentPromptBehaviorAdmin -Value 1 -PropertyType DWord -Force | Out-Null
    New-ItemProperty -Path $pol -Name PromptOnSecureDesktop -Value 1 -PropertyType DWord -Force | Out-Null

    # --- 3. The tree root, locked before anything is staged in it ----------------------------------------------------------------------
    if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent | Out-Null }
    $parentOwner = (Get-Acl $parent).GetOwner([Security.Principal.SecurityIdentifier])
    if ($parentOwner -eq $dailySid) { throw "${parent} is owned by ${daily}: an agent could swap what is inside. Delete it (after checking it) and run again." }
    New-Item -ItemType Directory -Path $root | Out-Null; $createdRoot = $true
    Set-Icacls $root @('/inheritance:r', '/grant:r', "*$($sid.Admins):(OI)(CI)(F)", "*$($sid.System):(OI)(CI)(F)")

    # --- 4. The account ----------------------------------------------------------------------------------------------------------------------
    $pw1 = Read-Host "Password for $acct" -AsSecureString
    $pw2 = Read-Host 'Repeat the password' -AsSecureString
    if ([Net.NetworkCredential]::new('', $pw1).Password -cne [Net.NetworkCredential]::new('', $pw2).Password) { throw 'The two passwords differ.' }
    New-LocalUser -Name $acct -Password $pw1 -PasswordNeverExpires -UserMayNotChangePassword -AccountNeverExpires -Description 'Runs the Backlog DDL tool and holds the owner DSN vault. Not an administrator.' | Out-Null
    $createdAccount = $true
    Add-LocalGroupMember -SID $sidOf.Users -Member $acct
    $ul = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\SpecialAccounts\UserList'
    New-Item -Path $ul -Force | Out-Null
    New-ItemProperty -Path $ul -Name $acct -Value 0 -PropertyType DWord -Force | Out-Null; $createdUserList = $true

    # --- 5. The vault (DSN typed once, no echo, never on a command line) --------------------------------------------------------------
    New-Item -ItemType Directory -Path $vault | Out-Null
    $dsnSecure = Read-Host 'Owner DSN (postgresql://role:password@127.0.0.1:5436/gestion)' -AsSecureString
    $dsn = [Net.NetworkCredential]::new('', $dsnSecure).Password
    if ($dsn -notmatch '^postgresql://[^:@/\s]+:[^@\s]+@127\.0\.0\.1:5436/gestion$') { throw 'The DSN must target exactly 127.0.0.1:5436/gestion.' }
    [IO.File]::WriteAllText($file, "FONDOS_PG_DSN_OWNER=$dsn`n", (New-Object Text.UTF8Encoding($false)))
    $dsn = $null; $dsnSecure = $null
    Invoke-Native 'auditpol.exe' @('/set', '/subcategory:{0CCE921D-69AE-11D9-BED3-505054503030}', '/success:enable', '/failure:enable')
    $rights = [Security.AccessControl.FileSystemRights]'ReadData, WriteData, Delete, ChangePermissions, TakeOwnership'
    $acl = Get-Acl -Path $vault -Audit
    $acl.AddAuditRule((New-Object Security.AccessControl.FileSystemAuditRule($sidOf.Everyone, $rights, 'ContainerInherit, ObjectInherit', 'None', 'Success, Failure')))
    Set-Acl -Path $vault -AclObject $acl
    Set-Icacls $vault @('/inheritance:r', '/grant:r', "${me}:(OI)(CI)(R)", '/deny', "*$($sid.System):(OI)(CI)(F)")

    # --- 6. The tool tree: ONE commit, extracted with git archive + tar; the interpreter copied next to it -------------------------------
    $stage = Join-Path $root 'stage.tar'
    Invoke-Native $gitExe (@('-c', "safe.directory=$Repo", '-C', $Repo, 'archive', '--format=tar', "--output=$stage", $Commit, '--') + $payload)
    New-Item -ItemType Directory -Path (Join-Path $root 'app'), (Join-Path $root 'runtime'), (Join-Path $root 'receipts') | Out-Null
    Invoke-Native $tar @('-xf', $stage, '-C', (Join-Path $root 'app'))
    Remove-Item $stage -Force
    $rt = Join-Path $root 'runtime'
    $rc = @('/NJH', '/NJS', '/NP', '/NFL', '/NDL', '/R:1', '/W:1')
    Invoke-Native $robo (@($PythonHome, $rt, 'python.exe', '*.dll') + $rc) -Ok (0..7)
    Invoke-Native $robo (@((Join-Path $PythonHome 'DLLs'), (Join-Path $rt 'DLLs'), '/E') + $rc) -Ok (0..7)
    Invoke-Native $robo (@((Join-Path $PythonHome 'Library\bin'), (Join-Path $rt 'Library\bin'), '/E') + $rc) -Ok (0..7)
    Invoke-Native $robo (@((Join-Path $PythonHome 'Lib'), (Join-Path $rt 'Lib'), '/E', '/XD', 'site-packages', '__pycache__', 'test', 'tests', 'idlelib', 'turtledemo') + $rc) -Ok (0..7)
    foreach ($pkg in 'psycopg', 'psycopg_binary', 'psycopg_binary.libs', 'tzdata') {
        Invoke-Native $robo (@((Join-Path $PythonHome "Lib\site-packages\$pkg"), (Join-Path $rt "Lib\site-packages\$pkg"), '/E', '/XD', '__pycache__') + $rc) -Ok (0..7)
    }
    Copy-Item (Join-Path $PythonHome 'Lib\site-packages\typing_extensions.py') (Join-Path $rt 'Lib\site-packages\typing_extensions.py')
    foreach ($need in 'python.exe', 'Lib\os.py', 'Lib\site-packages\psycopg\__init__.py', 'app\scripts\ops\db_owner_preflight.py', 'app\scripts\ops\windows\run_db_owner.cmd') {
        $p = if ($need.StartsWith('app')) { Join-Path $root $need } else { Join-Path $rt $need }
        if (-not (Test-Path $p)) { throw "tree incomplete: $need" }
    }
    $integrity = Test-RuntimeIntegrity $PythonHome $rt
    if ($integrity.Problems.Count -gt 0) { throw ("runtime integrity: $($integrity.Problems.Count) problem(s), first: " + (($integrity.Problems | Select-Object -First 10) -join '; ')) }
    Write-Host "Runtime: $($integrity.Verified) files match their install-time hashes (conda-meta, pip RECORD); no bytecode, .pth pointer or startup hook."

    # --- 7. MANIFEST.sha256 over app\ and runtime\ (format read by db_owner_manifest.py) ---------------------------------------------
    $lines = Get-ChildItem -Path $root -Recurse -File -Force | ForEach-Object {
        $rel = $_.FullName.Substring($root.Length + 1).Replace('\', '/')
        if ($rel -notmatch '^(vault|receipts)/' -and $rel -ne 'MANIFEST.sha256') { "$((Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant())  $rel" }
    }
    $sorted = [string[]]$lines; [Array]::Sort($sorted, [StringComparer]::Ordinal)
    $manifest = Join-Path $root 'MANIFEST.sha256'
    [IO.File]::WriteAllText($manifest, ("# commit $Commit`n" + ($sorted -join "`n") + "`n"), (New-Object Text.UTF8Encoding($false)))

    # --- 8. ACLs: the account reads and executes its code and can never write it; the daily user reads; receipts are the only writable place ----
    Set-Icacls $root @('/inheritance:r', '/grant:r', "*$($sid.Admins):(OI)(CI)(F)", "*$($sid.System):(OI)(CI)(RX)", "${me}:(OI)(CI)(RX)", "*$($dailySid.Value):(OI)(CI)(RX)")
    Set-Icacls $vault @('/inheritance:r', '/grant:r', "${me}:(OI)(CI)(R)", '/deny', "*$($sid.System):(OI)(CI)(F)")
    Set-Icacls (Join-Path $root 'receipts') @('/inheritance:r', '/grant:r', "*$($sid.Admins):(OI)(CI)(F)", "${me}:(OI)(CI)(M)", "*$($dailySid.Value):(OI)(CI)(RX)")

    # --- 9. Assertions: any failure undoes the whole run ---------------------------------------------------------------------------------
    $a = Get-Acl -Path $vault
    $rules = @($a.Access)
    if (-not $a.AreAccessRulesProtected) { throw 'ASSERT: the vault inherits from its parent.' }
    if ($rules.Count -ne 2 -or @($rules | Where-Object { $_.IsInherited }).Count -ne 0) { throw 'ASSERT: the vault must hold exactly two own entries.' }
    $allow = @($rules | Where-Object { $_.AccessControlType -eq 'Allow' }); $deny = @($rules | Where-Object { $_.AccessControlType -eq 'Deny' })
    if ($allow.Count -ne 1 -or $allow[0].IdentityReference.Value -ne $me) { throw 'ASSERT: the only allow entry on the vault must be fondos_db_owner.' }
    if ($deny.Count -ne 1 -or $deny[0].IdentityReference.Translate([Security.Principal.SecurityIdentifier]) -ne $sidOf.System) { throw 'ASSERT: the only deny entry on the vault must be SYSTEM.' }
    $code = Get-Acl -Path (Join-Path $root 'app')
    foreach ($r in @($code.Access | Where-Object { $_.IdentityReference.Value -eq $me })) {
        if ($r.FileSystemRights -band ([Security.AccessControl.FileSystemRights]'WriteData, AppendData, Delete, ChangePermissions, TakeOwnership')) { throw 'ASSERT: fondos_db_owner can write its own code.' }
    }
    foreach ($r in @($code.Access | Where-Object { $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]) -eq $dailySid })) {
        if ($r.FileSystemRights -band ([Security.AccessControl.FileSystemRights]'WriteData, AppendData, Delete, ChangePermissions, TakeOwnership')) { throw 'ASSERT: the daily user can write the tool tree.' }
    }
    if (@(Get-LocalGroupMember -SID $sidOf.Admins | ForEach-Object { $_.Name }) -contains $me) { throw 'ASSERT: fondos_db_owner is an administrator.' }
    if (@(Get-LocalGroupMember -SID $sidOf.Rdp | ForEach-Object { $_.Name }) -contains $me) { throw 'ASSERT: fondos_db_owner can use Remote Desktop.' }
    if (-not (Test-Path $manifest)) { throw 'ASSERT: no manifest.' }
    $mh = (Get-FileHash $manifest -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-Host "PROVISIONED OK. Commit $Commit. MANIFEST.sha256 sha256 = $mh (record it in deployments/). Content of the vault was never printed."
    Write-Host "Next, from your normal cmd.exe:  $root\app\scripts\ops\windows\run_db_owner.cmd --check"
}
catch {
    $err = $_
    Write-Host "FAILED: $($err.Exception.Message). Undoing what this run created." -ForegroundColor Red
    try {
        if ($createdUserList) { Remove-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\SpecialAccounts\UserList' -Name $acct -ErrorAction SilentlyContinue }
        if ($createdAccount) { Remove-LocalUser -Name $acct -ErrorAction SilentlyContinue }
        if ($createdRoot -and (Test-Path $root)) {
            Invoke-Native 'takeown.exe' @('/F', $root, '/R', '/D', 'Y') -Ok (0..1)
            Invoke-Native 'icacls.exe' @($root, '/grant', "*$($sid.Admins):(OI)(CI)(F)", '/T', '/C', '/Q') -Ok (0..1)
            Remove-Item $root -Recurse -Force -ErrorAction SilentlyContinue
        }
    } catch { Write-Host "Cleanup incomplete: $($_.Exception.Message). Review $root and the account $acct by hand." -ForegroundColor Red }
    $global:LASTEXITCODE = 1
    return
}
