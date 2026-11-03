# Synthetic checks only. Does not install/stop/delete a real service or set ACLs.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
. (Join-Path $repo 'packaging/inno_setup/install-support.ps1')

function Assert([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

foreach ($directory in @($false, $true)) {
    $acl = New-ProtectedAcl $directory $false
    $rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
    Assert ($acl.AreAccessRulesProtected) 'Data ACL must disable inheritance.'
    Assert ($rules.Count -eq 2) 'Only SYSTEM/Administrators may access Agent data.'
    foreach ($rule in $rules) {
        Assert ($rule.IdentityReference.Value -in @('S-1-5-18', 'S-1-5-32-544')) 'Unexpected principal.'
        Assert ($rule.FileSystemRights -eq 'FullControl') 'Expected full access for privileged identities.'
    }
}
$appAcl = New-ProtectedAcl $true $true
$userRule = @($appAcl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]) |
    Where-Object { $_.IdentityReference.Value -eq 'S-1-5-32-545' })
Assert ($userRule.Count -eq 1) 'Users need one application read/execute rule.'
$rights = $userRule[0].FileSystemRights
$readExecute = [Security.AccessControl.FileSystemRights]::ReadAndExecute
$write = [Security.AccessControl.FileSystemRights]::Write
Assert (($rights -band $readExecute) -eq $readExecute) 'Users must read/execute the application.'
Assert (($rights -band $write) -eq 0) 'Users must not write SYSTEM binaries.'

foreach ($protocol in @('Udp', 'Tcp')) {
    $type = if ($protocol -eq 'Udp') { 'Dgram' } else { 'Stream' }
    $socket = New-Object Net.Sockets.Socket(
        [Net.Sockets.AddressFamily]::InterNetwork,
        [Net.Sockets.SocketType]$type, [Net.Sockets.ProtocolType]$protocol)
    $socket.ExclusiveAddressUse = $true
    $socket.Bind((New-Object Net.IPEndPoint([Net.IPAddress]::Loopback, 0)))
    $port = $socket.LocalEndPoint.Port
    $detected = $false
    try { Test-DnsPort $port } catch {
        if ($_.Exception.Message -notlike 'Cannot bind*') { throw }
        $detected = $true
    } finally { $socket.Dispose() }
    Assert $detected "$protocol conflict was not detected."
    Test-DnsPort $port  # Both probe sockets must be released on failure too.
}

# An unrelated service must never be stopped/reconfigured just by name.
function Get-CimInstance {
    param([string]$ClassName, [string]$Filter)
    return [pscustomobject]@{ PathName = '"C:\Unrelated\service.exe"' }
}
$rejected = $false
try { Get-OwnedService 'C:\Program Files\OpenGuardKids\agent\ogk_agent.exe' } catch {
    Assert ($_.Exception.Message -like 'Service name is already owned*') 'Unexpected ownership error.'
    $rejected = $true
}
Assert $rejected 'A colliding service name must be rejected.'

# Substitute SCM, ACL and socket operations before testing preparation recovery.
function Assert-Admin {}
function Assert-NoReparse([string]$Path) {}
function Protect-Tree([string]$Path, [bool]$UserRead) {}
function Get-OwnedService([string]$Executable) { return [pscustomobject]@{ State = 'Running' } }
function Stop-OwnedService([string]$Executable) { $script:stopped = $true }
function Start-OwnedService([string]$Executable) { $script:restored = $true }
function Test-DnsPort {
    if ($script:conflict) { throw 'synthetic DNS conflict' }
}
$InstallDir = Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'OpenGuardKids'
$Action = 'Prepare'
$script:conflict = $false
$script:stopped = $false
$script:restored = $false
Assert ((Invoke-InstallAction) -eq 10) 'Prepare must remember a previously running service.'
Assert $script:stopped 'Existing service must stop before port probe and replacement.'
Assert (-not $script:restored) 'Successful preparation must leave the service stopped.'
$script:conflict = $true
try { Invoke-InstallAction; throw 'Expected preparation failure.' } catch {
    Assert ($_.Exception.Message -eq 'synthetic DNS conflict') 'Unexpected failure.'
}
Assert $script:restored 'Failed preflight must restore a previously running service.'

# Verify configuration orchestration without invoking New-Service/sc.exe/SCM.
$script:existing = $false
$script:createdPath = ''
$script:scCommands = New-Object 'System.Collections.Generic.List[string]'
function Get-OwnedService([string]$Executable) {
    if ($script:existing) { return [pscustomobject]@{ State = 'Running' } }
}
function Test-Path { param([string]$LiteralPath, [string]$PathType); return $true }
function New-Service {
    param([string]$Name, [string]$BinaryPathName, [string]$DisplayName, [string]$StartupType)
    $script:createdPath = $BinaryPathName
    $script:existing = $true
}
function Invoke-Sc([string[]]$Arguments) { $script:scCommands.Add(($Arguments -join '|')) }
function Start-Sleep { param([int]$Seconds, [int]$Milliseconds) }
$Action = 'Configure'
Assert ((Invoke-InstallAction) -eq 0) 'Configuration must complete.'
Assert ($script:createdPath -eq ('"{0}\agent\ogk_agent.exe"' -f $InstallDir)) 'SCM binary path must be quoted.'
Assert ($script:scCommands.Count -eq 4) 'Configuration/recovery commands missing.'
Assert ($script:scCommands[0] -eq 'config|OpenGuardKidsAgent|type=|own|start=|auto|obj=|LocalSystem') 'Wrong service account/start mode.'
Assert ($script:scCommands[2] -like '*restart/5000/restart/15000/restart/60000') 'Wrong recovery delays.'
Assert ($script:scCommands[3] -eq 'failureflag|OpenGuardKidsAgent|1') 'Non-crash failure recovery missing.'
Write-Output 'PASS: ACLs, UDP/TCP conflicts, service ownership, preparation recovery, SYSTEM/autostart/recovery orchestration.'
