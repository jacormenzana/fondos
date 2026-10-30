<#
 FND-0353, spec section 7 (external part), decision D6. Configures THIS machine as a Windows Event Forwarding SOURCE (source-initiated, HTTPS with certificates: the machine is not in a domain) so that
 the events the spec requires reach a collector on ANOTHER machine within 60 seconds, with a heartbeat every minute:
     4656, 4663, 4660, 4670 for the App key file and its directory, plus 1102 (audit log cleared) and 4719 (audit policy changed).
 The collector, its server certificate, its subscription and the immutable (WORM) store behind it are NOT created by this script: they are a different machine (spec: "must not be a service on the
 same machine"). The script emits the subscription XML to import there (wecutil cs) and refuses to run without the collector's name and both certificate thumbprints.

 Two calls, IN MEMORY from the pinned commit (doc/operativos/LOG_SINK_RUNBOOK.md):
   & ([scriptblock]::Create($text)) -CollectorHost ... -ClientCertThumbprint ... -IssuerCaThumbprint ...            DRY RUN: every precondition and the plan, nothing changed
   & ([scriptblock]::Create($text)) -CollectorHost ... -ClientCertThumbprint ... -IssuerCaThumbprint ... -Apply     the change; any failure undoes what this run changed
 It sets the Security log to at least 256 MB, turns on the audit subcategories that produce the events, starts WinRM, lets NETWORK SERVICE (the identity that forwards; neither the runner nor the
 owner) read the Security log and the client certificate's private key, and writes the SubscriptionManager policy. It never opens a listener or a firewall port: the machine only connects OUT.
#>
param(
    [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$')][string]$CollectorHost,
    [Parameter(Mandatory)][ValidatePattern('^[0-9A-Fa-f]{40}$')][string]$ClientCertThumbprint,
    [Parameter(Mandatory)][ValidatePattern('^[0-9A-Fa-f]{40}$')][string]$IssuerCaThumbprint,
    [ValidateRange(1, 65535)][int]$CollectorPort = 5986,
    [ValidateRange(30, 3600)][int]$RefreshSeconds = 60,
    [string]$KeyFile = 'C:\ProgramData\fondos\ci_runner\github_app_keys\fondos-pr-opener.pem',
    [string]$KeyDir = 'C:\ProgramData\fondos\ci_runner\github_app_keys',
    [string]$SubscriptionOut = 'C:\ProgramData\fondos\log_sink\fondos-key-access.xml',
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
$ClientCertThumbprint = $ClientCertThumbprint.ToUpperInvariant()
$IssuerCaThumbprint = $IssuerCaThumbprint.ToUpperInvariant()
$SubscriptionId = 'fondos-key-access'
$RegKey = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\EventLog\EventForwarding\SubscriptionManager'
$MinLogBytes = 268435456
$sidReaders = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-573')       # Event Log Readers
$sidNetSvc  = New-Object Security.Principal.SecurityIdentifier('S-1-5-20')           # NETWORK SERVICE
$sidAdmins  = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')

function Invoke-Native {
    param([string]$Exe, [string[]]$ArgList, [int[]]$Ok = @(0))
    & $Exe @ArgList | Out-Null
    if ($Ok -notcontains $LASTEXITCODE) { throw "$Exe failed with exit code $LASTEXITCODE" }
}
function ConvertTo-XmlText { param([string]$Text) [Security.SecurityElement]::Escape($Text) }

function Get-EventQuery {
    # The Windows event-log XPath dialect has no contains(): the object names are matched exactly (the key file, and its directory for a listing attempt).
    param([string]$File, [string]$Dir)
    $f = ConvertTo-XmlText $File; $d = ConvertTo-XmlText $Dir
    return @"
<QueryList><Query Id="0" Path="Security">
<Select Path="Security">*[System[(EventID=1102 or EventID=4719)]]</Select>
<Select Path="Security">*[System[(EventID=4656 or EventID=4663 or EventID=4660 or EventID=4670)] and EventData[Data[@Name='ObjectName']='$f' or Data[@Name='ObjectName']='$d']]</Select>
</Query></QueryList>
"@
}
function New-SubscriptionXml {
    param([string]$File, [string]$Dir, [string]$IssuerThumbprint, [string]$Id)
    $q = Get-EventQuery $File $Dir
    return @"
<?xml version="1.0" encoding="UTF-8"?>
<Subscription xmlns="http://schemas.microsoft.com/2006/03/windows/events/subscription">
  <SubscriptionId>$Id</SubscriptionId>
  <SubscriptionType>SourceInitiated</SubscriptionType>
  <Description>FND-0353 D6: access to the GitHub App key, audit log cleared, audit policy changed. Forwarded off the machine within 60 s, heartbeat every 60 s.</Description>
  <Enabled>true</Enabled>
  <Uri>http://schemas.microsoft.com/wbem/wsman/1/windows/EventLog</Uri>
  <ConfigurationMode>Custom</ConfigurationMode>
  <Delivery Mode="Push">
    <Batching><MaxLatencyTime>30000</MaxLatencyTime></Batching>
    <PushSettings><Heartbeat Interval="60000"/></PushSettings>
  </Delivery>
  <Query><![CDATA[$q]]></Query>
  <ReadExistingEvents>false</ReadExistingEvents>
  <TransportName>HTTPS</TransportName>
  <ContentFormat>RenderedText</ContentFormat>
  <Locale Language="en-US"/>
  <LogFile>ForwardedEvents</LogFile>
  <AllowedSourceNonDomainComputers>
    <AllowedIssuerCAList><AllowedIssuerCA>$IssuerThumbprint</AllowedIssuerCA></AllowedIssuerCAList>
  </AllowedSourceNonDomainComputers>
  <AllowedSourceDomainComputers></AllowedSourceDomainComputers>
</Subscription>
"@
}
function Get-ManagerString {
    param([string]$HostName, [int]$Port, [int]$Refresh, [string]$IssuerThumbprint)
    return "Server=HTTPS://${HostName}:${Port}/wsman/SubscriptionManager/WEC,Refresh=${Refresh},IssuerCA=${IssuerThumbprint}"
}

$plan = New-Object System.Collections.Generic.List[string]
$undo = New-Object System.Collections.Generic.List[scriptblock]
try {
    if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run this in an elevated PowerShell (Run as administrator).' }

    # --- preconditions (read-only) -----------------------------------------------------------------------------------------------------------------
    if ($CollectorHost -in @($env:COMPUTERNAME, 'localhost', '127.0.0.1', '::1') -or $CollectorHost -like "$($env:COMPUTERNAME).*") { throw 'The collector must be ANOTHER machine (spec section 7): this host cannot be its own sink.' }
    $cert = Get-Item -LiteralPath "Cert:\LocalMachine\My\$ClientCertThumbprint" -ErrorAction SilentlyContinue
    if (-not $cert) { throw "Client certificate $ClientCertThumbprint is not in LocalMachine\My." }
    if (-not $cert.HasPrivateKey) { throw 'The client certificate has no private key on this machine.' }
    if ($cert.NotAfter -lt (Get-Date).AddDays(30)) { throw "The client certificate expires on $($cert.NotAfter): renew it first." }
    if (-not (@($cert.EnhancedKeyUsageList | ForEach-Object { $_.ObjectId }) -contains '1.3.6.1.5.5.7.3.2')) { throw 'The client certificate lacks the Client Authentication usage (1.3.6.1.5.5.7.3.2).' }
    $ca = @(Get-ChildItem Cert:\LocalMachine\Root, Cert:\LocalMachine\CA -ErrorAction SilentlyContinue | Where-Object { $_.Thumbprint -eq $IssuerCaThumbprint })
    if ($ca.Count -eq 0) { throw "The issuing CA $IssuerCaThumbprint is not in LocalMachine\Root or \CA." }
    if ($cert.Issuer -ne $ca[0].Subject) { throw 'The client certificate was not issued by that CA.' }
    if (-not (Test-NetConnection -ComputerName $CollectorHost -Port $CollectorPort -InformationLevel Quiet -WarningAction SilentlyContinue)) { throw "Cannot reach ${CollectorHost}:${CollectorPort}. The sink must be reachable before this machine is told to use it." }
    $existing = @()
    if (Test-Path $RegKey) { $existing = @((Get-Item $RegKey).GetValueNames() | Where-Object { $_ }) }
    if ($existing.Count -gt 0) { throw "A SubscriptionManager is already configured ($($existing -join ', ')): another collector may be receiving events. Review it by hand." }
    $readers = @(Get-LocalGroupMember -SID $sidReaders | ForEach-Object { $_.SID.Value })
    foreach ($acct in 'fondos_ci_runner', 'fondos_db_owner') {
        $u = Get-LocalUser -Name $acct -ErrorAction SilentlyContinue
        if ($u -and ($readers -contains $u.SID.Value)) { throw "$acct is a member of Event Log Readers: the forwarder must be its own identity." }
    }
    $manager = Get-ManagerString $CollectorHost $CollectorPort $RefreshSeconds $IssuerCaThumbprint
    $xml = New-SubscriptionXml $KeyFile $KeyDir $IssuerCaThumbprint $SubscriptionId
    [void][xml]$xml                                                                                   # well-formed or stop
    $secLog = Get-WinEvent -ListLog Security
    $winrm = Get-Service -Name WinRM
    $plan.Add("Security log maximum size: $([math]::Round($secLog.MaximumSizeInBytes / 1MB)) MB -> at least $([math]::Round($MinLogBytes / 1MB)) MB")
    $plan.Add('Audit subcategories File System and Audit Policy Change: success and failure on')
    $plan.Add("WinRM service: $($winrm.Status) / $($winrm.StartType) -> Running / Automatic (no listener, no firewall rule)")
    $plan.Add("NETWORK SERVICE: member of Event Log Readers $(if ($readers -contains $sidNetSvc.Value) { '(already)' } else { '(added)' }); read access to the private key of $ClientCertThumbprint")
    $plan.Add("SubscriptionManager policy: $manager")
    $plan.Add("Subscription XML for the collector: $SubscriptionOut")
    Write-Host 'Preconditions passed. Plan:'; $plan | ForEach-Object { Write-Host "  - $_" }
    if (-not $Apply) { Write-Host 'DRY RUN: nothing was changed. Repeat with -Apply.'; $global:LASTEXITCODE = 0; return }

    # --- apply ---------------------------------------------------------------------------------------------------------------------------------------
    foreach ($g in '{0CCE921D-69AE-11D9-BED3-505054503030}', '{0CCE922F-69AE-11D9-BED3-505054503030}') {                 # File System, Audit Policy Change
        Invoke-Native 'auditpol.exe' @('/set', "/subcategory:$g", '/success:enable', '/failure:enable')
    }
    if ($secLog.MaximumSizeInBytes -lt $MinLogBytes) {
        $old = $secLog.MaximumSizeInBytes
        Invoke-Native 'wevtutil.exe' @('sl', 'Security', "/ms:$MinLogBytes")
        $undo.Add({ Invoke-Native 'wevtutil.exe' @('sl', 'Security', "/ms:$old") }.GetNewClosure())
    }
    $oldType, $oldStatus = $winrm.StartType, $winrm.Status
    Set-Service -Name WinRM -StartupType Automatic
    $undo.Add({ Set-Service -Name WinRM -StartupType $oldType }.GetNewClosure())
    if ($oldStatus -ne 'Running') { Start-Service -Name WinRM; $undo.Add({ Stop-Service -Name WinRM -ErrorAction SilentlyContinue }.GetNewClosure()) }
    if ($readers -notcontains $sidNetSvc.Value) {
        Add-LocalGroupMember -SID $sidReaders -Member $sidNetSvc.Value
        $undo.Add({ Remove-LocalGroupMember -SID $sidReaders -Member $sidNetSvc.Value -ErrorAction SilentlyContinue }.GetNewClosure())
    }
    # the forwarder (NETWORK SERVICE) must be able to use the client certificate's private key
    $rsa = [Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($cert)
    $unique = $rsa.Key.UniqueName
    $keyFile = @('C:\ProgramData\Microsoft\Crypto\Keys', 'C:\ProgramData\Microsoft\Crypto\RSA\MachineKeys') | ForEach-Object { Join-Path $_ $unique } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $keyFile) { throw "Cannot find the key container file of the client certificate ($unique): grant NETWORK SERVICE read access to it by hand, then re-run." }
    Invoke-Native 'icacls.exe' @($keyFile, '/grant', '*S-1-5-20:(R)')
    $undo.Add({ Invoke-Native 'icacls.exe' @($keyFile, '/remove:g', '*S-1-5-20') -Ok (0..1) }.GetNewClosure())
    New-Item -Path $RegKey -Force | Out-Null
    New-ItemProperty -Path $RegKey -Name '1' -Value $manager -PropertyType String -Force | Out-Null
    $undo.Add({ Remove-ItemProperty -Path $RegKey -Name '1' -ErrorAction SilentlyContinue }.GetNewClosure())
    Restart-Service -Name WinRM -Force
    New-Item -ItemType Directory -Path (Split-Path -Parent $SubscriptionOut) -Force | Out-Null
    [IO.File]::WriteAllText($SubscriptionOut, $xml, (New-Object Text.UTF8Encoding($false)))

    # --- assertions -----------------------------------------------------------------------------------------------------------------------------------
    if ((Get-Service -Name WinRM).Status -ne 'Running' -or (Get-Service -Name WinRM).StartType -ne 'Automatic') { throw 'ASSERT: WinRM is not running and automatic.' }
    if ((Get-WinEvent -ListLog Security).MaximumSizeInBytes -lt $MinLogBytes) { throw 'ASSERT: the Security log is below 256 MB.' }
    if ((Get-ItemProperty -Path $RegKey -Name '1').'1' -ne $manager) { throw 'ASSERT: the SubscriptionManager policy was not written.' }
    if (@(Get-LocalGroupMember -SID $sidReaders | ForEach-Object { $_.SID.Value }) -notcontains $sidNetSvc.Value) { throw 'ASSERT: NETWORK SERVICE is not in Event Log Readers.' }
    if (-not (Test-Path -LiteralPath $SubscriptionOut)) { throw 'ASSERT: the subscription XML was not written.' }
    Write-Host "LOG SINK SOURCE CONFIGURED. Import $SubscriptionOut on the collector (wecutil cs), then prove delivery with the steps of doc/operativos/LOG_SINK_RUNBOOK.md (acceptance 5). Nothing was sent yet: this machine is only ready to forward."
}
catch {
    $err = $_
    Write-Host "FAILED: $($err.Exception.Message)" -ForegroundColor Red
    if ($undo.Count -gt 0) {
        Write-Host 'Undoing what this run changed (the audit subcategories stay on: enabling auditing is harmless and idempotent).' -ForegroundColor Red
        for ($i = $undo.Count - 1; $i -ge 0; $i--) { try { & $undo[$i] } catch { Write-Host "Undo step failed: $($_.Exception.Message). Review by hand." -ForegroundColor Red } }
    }
    $global:LASTEXITCODE = 1
    return
}
