# Windows PowerShell 5.1, called only by the elevated Inno installer.
[CmdletBinding()]
param(
    [ValidateSet('Prepare', 'Configure', 'Restore', 'Remove')][string]$Action,
    [string]$InstallDir,
    [string]$LogPath
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ServiceName = 'OpenGuardKidsAgent'

function Write-InstallLog([string]$Message) {
    if ($LogPath) { Add-Content -LiteralPath $LogPath -Value $Message -Encoding UTF8 }
}

function Assert-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Administrator/UAC approval is required.'
    }
    if (-not [Environment]::Is64BitProcess) { throw 'Use native x64 PowerShell.' }
}

function Assert-NoReparse([string]$Path) {
    # Check ancestors too. Never traverse a pre-created junction as Administrator.
    $cursor = [IO.Path]::GetFullPath($Path)
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Reparse point is not allowed: $cursor"
            }
        }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
}

function New-ProtectedAcl([bool]$Directory, [bool]$UserRead) {
    if ($Directory) {
        $acl = New-Object Security.AccessControl.DirectorySecurity
        $inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
    } else {
        $acl = New-Object Security.AccessControl.FileSecurity
        $inherit = [Security.AccessControl.InheritanceFlags]::None
    }
    # A fresh DACL drops explicit grants too, unlike merely disabling inheritance.
    $acl.SetAccessRuleProtection($true, $false)
    $admins = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')
    $acl.SetOwner($admins)
    foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
        $identity = New-Object Security.Principal.SecurityIdentifier($sid)
        $rule = New-Object Security.AccessControl.FileSystemAccessRule(
            $identity, 'FullControl', $inherit, 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    if ($UserRead) {
        $users = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-545')
        $rule = New-Object Security.AccessControl.FileSystemAccessRule(
            $users, 'ReadAndExecute', $inherit, 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    return $acl
}

function Protect-Tree([string]$Path, [bool]$UserRead) {
    Assert-NoReparse $Path
    if (-not (Test-Path -LiteralPath $Path)) {
        # Full .NET Framework overload sets the ACL at creation, before SQLite use.
        [IO.Directory]::CreateDirectory($Path, (New-ProtectedAcl $true $UserRead)) | Out-Null
    }
    $root = Get-Item -LiteralPath $Path -Force
    if (-not $root.PSIsContainer) { throw "Expected a directory: $Path" }
    Set-Acl -LiteralPath $Path -AclObject (New-ProtectedAcl $true $UserRead)
    # Enumerate one level at a time; do not recurse through junctions/symlinks.
    foreach ($item in Get-ChildItem -LiteralPath $Path -Force) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Reparse point is not allowed: $($item.FullName)"
        }
        if ($item.PSIsContainer) { Protect-Tree $item.FullName $UserRead }
        else { Set-Acl -LiteralPath $item.FullName -AclObject (New-ProtectedAcl $false $UserRead) }
    }
}

function Test-DnsPort([int]$Port = 53) {
    $sockets = @()
    try {
        foreach ($protocol in @('Udp', 'Tcp')) {
            $type = if ($protocol -eq 'Udp') { 'Dgram' } else { 'Stream' }
            $socket = New-Object Net.Sockets.Socket(
                [Net.Sockets.AddressFamily]::InterNetwork,
                [Net.Sockets.SocketType]$type, [Net.Sockets.ProtocolType]$protocol)
            $sockets += $socket
            $socket.ExclusiveAddressUse = $true
            $socket.Bind((New-Object Net.IPEndPoint([Net.IPAddress]::Loopback, $Port)))
        }
    } catch {
        throw "Cannot bind 127.0.0.1:$Port (UDP/TCP). Resolve the DNS port conflict before installing. $($_.Exception.Message)"
    } finally {
        foreach ($socket in $sockets) { $socket.Dispose() }
    }
}

function Get-OwnedService([string]$Executable) {
    $service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'"
    if ($service -and $service.PathName -ine ('"{0}"' -f $Executable)) {
        throw "Service name is already owned by another binary: $ServiceName"
    }
    return $service
}

function Stop-OwnedService([string]$Executable) {
    if (Get-OwnedService $Executable) {
        $controller = Get-Service -Name $ServiceName
        try {
            if ($controller.Status -ne 'Stopped') {
                Stop-Service -InputObject $controller
                $controller.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30))
            }
        } finally { $controller.Dispose() }
    }
}

function Start-OwnedService([string]$Executable) {
    if (Get-OwnedService $Executable) {
        $controller = Get-Service -Name $ServiceName
        try {
            Start-Service -InputObject $controller
            $controller.WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
        } finally { $controller.Dispose() }
    }
}

function Invoke-Sc([string[]]$Arguments) {
    $output = & "$env:SystemRoot\System32\sc.exe" @Arguments 2>&1
    $code = $LASTEXITCODE
    Write-InstallLog ($output -join [Environment]::NewLine)
    if ($code -ne 0) { throw "sc.exe failed ($code): $($Arguments -join ' ')" }
}

function Invoke-InstallAction {
    Assert-Admin
    $expected = Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'OpenGuardKids'
    if ([IO.Path]::GetFullPath($InstallDir).TrimEnd('\') -ine $expected) {
        throw "Install path must be the protected directory: $expected"
    }
    $data = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'OpenGuardKids'
    $executable = Join-Path $InstallDir 'agent\ogk_agent.exe'
    Assert-NoReparse $InstallDir
    Assert-NoReparse $data
    switch ($Action) {
        'Prepare' {
            $old = Get-OwnedService $executable
            $wasRunning = $old -and $old.State -ne 'Stopped'
            try {
                Stop-OwnedService $executable
                Test-DnsPort
                Protect-Tree $InstallDir $true
                Protect-Tree $data $false
                foreach ($leaf in @('state', 'cache', 'logs', 'config')) {
                    Protect-Tree (Join-Path $data $leaf) $false
                }
            } catch {
                if ($wasRunning) { Start-OwnedService $executable }
                throw
            }
            if ($wasRunning) { return 10 }
        }
        'Configure' {
            if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) { throw 'Agent binary missing.' }
            Protect-Tree $InstallDir $true
            Protect-Tree $data $false
            if (-not (Get-OwnedService $executable)) {
                New-Service -Name $ServiceName -BinaryPathName ('"{0}"' -f $executable) `
                    -DisplayName 'OpenGuard Kids Agent' -StartupType Automatic | Out-Null
            }
            Invoke-Sc @('config', $ServiceName, 'type=', 'own', 'start=', 'auto', 'obj=', 'LocalSystem')
            Invoke-Sc @('description', $ServiceName,
                'OpenGuard Kids parental controls. Visible User Tray required; waiting for enrollment until paired.')
            Invoke-Sc @('failure', $ServiceName, 'reset=', '86400', 'actions=', 'restart/5000/restart/15000/restart/60000')
            Invoke-Sc @('failureflag', $ServiceName, '1')
            try {
                Start-OwnedService $executable
                Start-Sleep -Seconds 2
                if ((Get-OwnedService $executable).State -ne 'Running') { throw 'Service failed after startup.' }
            } catch {
                Stop-OwnedService $executable
                throw
            }
        }
        'Restore' { Start-OwnedService $executable }
        'Remove' {
            Stop-OwnedService $executable
            if (Get-OwnedService $executable) {
                Invoke-Sc @('delete', $ServiceName)
                $deadline = [DateTime]::UtcNow.AddSeconds(30)
                while (Get-CimInstance Win32_Service -Filter "Name='$ServiceName'") {
                    if ([DateTime]::UtcNow -gt $deadline) { throw 'Service deletion pending; close Services console and retry.' }
                    Start-Sleep -Milliseconds 250
                }
            }
            # Preserve ProgramData: removal is not the authenticated data-purge workflow.
            # No DNS/hosts changes are made by the present runtime or this installer.
        }
    }
    return 0
}

# Dot sourcing loads functions for synthetic tests without touching SCM or ACLs.
if ($MyInvocation.InvocationName -ne '.') {
    try {
        if (-not $Action) { throw 'Action is required.' }
        $code = Invoke-InstallAction
        exit $code
    } catch {
        Write-InstallLog $_.Exception.Message
        exit 1
    }
}
