[CmdletBinding()]
param(
    [string]$Python = 'python',
    [string]$Iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    [ValidatePattern('^\d+\.\d+\.\d+(\.\d+)?$')][string]$Version = '0.1.0'
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent

function Invoke-Python([string[]]$Arguments) {
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Python build command failed ($LASTEXITCODE)." }
}

Push-Location $repo
try {
    # Fail early on Store aliases, wrong architecture, and missing build tools.
    Invoke-Python @('-c', "import sys, struct; assert sys.platform == 'win32' and struct.calcsize('P') == 8; assert sys.version_info >= (3, 11)")
    if (-not (Test-Path -LiteralPath $Iscc -PathType Leaf)) { throw "Inno compiler not found: $Iscc" }
    Invoke-Python @('-m', 'pip', 'install', '-r', 'packaging/requirements-build.txt',
        './packages/agent', './packages/interfaces[tray]')
    foreach ($name in @('ogk_agent', 'ogk_ui')) {
        Invoke-Python @('-m', 'PyInstaller', '--noconfirm', '--clean',
            '--distpath', 'dist', '--workpath', 'build/pyinstaller', "packaging/pyinstaller/$name.spec")
        $exe = Join-Path $repo "dist\$name\$name.exe"
        $process = Start-Process -FilePath $exe -ArgumentList '--smoke-test' `
            -WindowStyle Hidden -PassThru
        try {
            if (-not $process.WaitForExit(30000)) {
                $process.Kill()
                throw "$name frozen import smoke check timed out."
            }
            if ($process.ExitCode -ne 0) { throw "$name frozen import smoke check failed ($($process.ExitCode))." }
        } finally { $process.Dispose() }
    }
    & $Iscc "/DAppVersion=$Version" 'packaging/inno_setup/OpenGuardKids.iss'
    if ($LASTEXITCODE -ne 0) { throw "Inno compilation failed ($LASTEXITCODE)." }
    $setup = Join-Path $repo 'dist\installer\OpenGuardKidsSetup.exe'
    Get-FileHash -LiteralPath $setup -Algorithm SHA256
    Write-Host "Built: $setup"
} finally { Pop-Location }
