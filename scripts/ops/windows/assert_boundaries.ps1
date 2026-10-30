<#
 FND-0353 / FND-0354. Runs the ten isolation checks of doc/operativos/PROVISIONING_RUNBOOK.md as an ordinary, NON-elevated process, i.e. with exactly the token an agent session has,
 and writes a structured receipt: deployments/<utc date>_boundary-assertions_<computer>.json (+ .sha256, + .sig when -SshKey is given).
 A check passes only on the specific outcome: "denied" means .NET raised UnauthorizedAccessException (Win32 error 5). A missing path, a sharing violation or any other error is a FAIL,
 never a pass, so a mistyped path or an unprovisioned machine cannot produce a green receipt.
 Exit 0 = every check passed, 1 = at least one failed, 2 = refused to run (elevated token).
 What the receipt is and is not: it is a repeatable, machine-readable record of what a non-elevated process of this user could and could not do, with the token facts and the script's own
 hash. It is evidence, not a proof of isolation: anything running as this user could also produce a file of this shape, which is why the receipt is committed to the repository
 (reviewed, versioned) and why the owner's signature (-SshKey, ssh-keygen -Y sign, namespace fondos-boundaries) is optional, not a security boundary.
#>
param(
    [string]$CiRoot   = 'C:\ProgramData\fondos\ci_runner',
    [string]$DbRoot   = 'C:\ProgramData\fondos\db_owner',
    [string]$RepoRoot = 'C:\desarrollo\fondos',
    [string]$EnvFile  = '',
    [string]$OutDir   = '',
    [string]$SshKey   = ''
)
$ErrorActionPreference = 'Stop'
if (-not $EnvFile) { $EnvFile = Join-Path $RepoRoot '.env' }
if (-not $OutDir)  { $OutDir  = Join-Path $RepoRoot 'deployments' }
$identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { Write-Host 'REFUSED: this token is elevated. Run it from a normal (non-elevated) terminal: the point is to test what an unprivileged process can do.'; exit 2 }

function Get-Outcome {
    # runs $Action and classifies the result: OK, DENIED (UnauthorizedAccessException) or the name of any other exception
    param([scriptblock]$Action)
    try { & $Action | Out-Null; return 'OK' }
    catch [UnauthorizedAccessException] { return 'DENIED' }
    catch { $e = $_.Exception; while ($e.InnerException) { $e = $e.InnerException }; return $e.GetType().Name }
}
function Test-Denied { param([string]$Id, [string]$Text, [scriptblock]$Action)
    $o = Get-Outcome $Action
    [pscustomobject][ordered]@{ id = $Id; check = $Text; expected = 'DENIED'; observed = $o; pass = ($o -eq 'DENIED') }
}

$results = New-Object System.Collections.Generic.List[object]
$keyDir = Join-Path $CiRoot 'github_app_keys'
$pem    = Join-Path $keyDir 'fondos-pr-opener.pem'
$vault  = Join-Path $DbRoot 'vault'
$conf   = Join-Path $vault 'owner_dsn.conf'
function Add-Write { param([string]$Dir) $p = Join-Path $Dir ('probe_' + [guid]::NewGuid().ToString('N') + '.tmp'); $s = [IO.File]::Create($p); $s.Close(); Remove-Item -LiteralPath $p -Force }

$results.Add((Test-Denied 'B01' 'list the CI key directory' { Get-ChildItem -LiteralPath $keyDir -ErrorAction Stop }))
$results.Add((Test-Denied 'B02' 'read the CI App private key' { $s = [IO.File]::OpenRead($pem); $s.Close() }))
$results.Add((Test-Denied 'B03' 'write into the CI outbox (forged receipt)' { Add-Write (Join-Path $CiRoot 'outbox') }))
$results.Add((Test-Denied 'B04' 'write into the CI runner code (app)' { Add-Write (Join-Path $CiRoot 'app') }))
$o = Get-Outcome { Add-Write (Join-Path $CiRoot 'inbox') }
$results.Add([pscustomobject][ordered]@{ id = 'B05'; check = 'write into the CI inbox (the only door)'; expected = 'OK'; observed = $o; pass = ($o -eq 'OK') })
$results.Add((Test-Denied 'B06' 'read the owner DSN vault file' { $s = [IO.File]::OpenRead($conf); $s.Close() }))
$results.Add((Test-Denied 'B07' 'list the owner DSN vault directory' { Get-ChildItem -LiteralPath $vault -ErrorAction Stop }))
$results.Add((Test-Denied 'B08' 'write into the DDL tool code (app)' { Add-Write (Join-Path $DbRoot 'app') }))

$admins = $null
try { $admins = @(Get-LocalGroupMember -SID (New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')) | ForEach-Object { $_.Name }) } catch { $admins = $null }
if ($null -eq $admins) { $obs = 'GROUP_UNREADABLE'; $ok = $false }
else { $hit = @($admins | Where-Object { $_ -match '\\(fondos_ci_runner|fondos_db_owner)$' }); $obs = if ($hit.Count) { 'LISTED: ' + ($hit -join ',') } else { 'ABSENT' }; $ok = ($hit.Count -eq 0) }
$results.Add([pscustomobject][ordered]@{ id = 'B09'; check = 'neither service account is an administrator'; expected = 'ABSENT'; observed = $obs; pass = $ok })

if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) { $obs = 'ENV_FILE_MISSING'; $ok = $false }
else { $present = @(Select-String -LiteralPath $EnvFile -Pattern '^\s*FONDOS_PG_DSN_OWNER\s*=' -ErrorAction Stop).Count -gt 0; $obs = if ($present) { 'PRESENT' } else { 'ABSENT' }; $ok = -not $present }
$results.Add([pscustomobject][ordered]@{ id = 'B10'; check = 'no owner DSN key in the repository .env (names only, no value read)'; expected = 'ABSENT'; observed = $obs; pass = $ok })

$allPass = (@($results | Where-Object { -not $_.pass }).Count -eq 0)
$overall = if ($allPass) { 'PASS' } else { 'FAIL' }
$scriptHash = (Get-FileHash -LiteralPath $MyInvocation.MyCommand.Path -Algorithm SHA256).Hash.ToLowerInvariant()
$head = ''
try { $head = (& git -C $RepoRoot rev-parse HEAD 2>$null) } catch { $head = '' }
$receipt = [ordered]@{
    schema      = 'fondos.boundary-receipt/1'
    utc         = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    computer    = $env:COMPUTERNAME
    user_sid    = $identity.User.Value
    elevated    = $false
    script_sha256 = $scriptHash
    repo_head   = "$head"
    overall     = $overall
    results     = $results.ToArray()
}
if (-not (Test-Path -LiteralPath $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }
$name = '{0}_boundary-assertions_{1}.json' -f (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ'), $env:COMPUTERNAME
$file = Join-Path $OutDir $name
$json = (($receipt | ConvertTo-Json -Depth 5) -replace "`r`n", "`n") + "`n"
[IO.File]::WriteAllText($file, $json, (New-Object Text.UTF8Encoding($false)))
$hash = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText("$file.sha256", "$hash  $name`n", (New-Object Text.UTF8Encoding($false)))
if ($SshKey) {
    if (-not (Get-Command ssh-keygen.exe -ErrorAction SilentlyContinue)) { Write-Host 'ssh-keygen.exe not found: the receipt is NOT signed.' }
    else { & ssh-keygen.exe -Y sign -f $SshKey -n fondos-boundaries $file | Out-Null; if ($LASTEXITCODE -ne 0) { Write-Host 'Signing failed: the receipt is NOT signed.' } else { Write-Host "Signed: $file.sig" } }
}
foreach ($r in $results) { '{0}  {1,-4}  {2,-62} expected {3,-7} observed {4}' -f $(if ($r.pass) { 'PASS' } else { 'FAIL' }), $r.id, $r.check, $r.expected, $r.observed }
Write-Host "OVERALL: $($receipt.overall)  receipt: $file  sha256: $hash"
if ($allPass) { exit 0 } else { exit 1 }
