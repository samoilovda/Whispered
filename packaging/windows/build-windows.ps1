[CmdletBinding()]
param(
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT") { throw "Run this script on Windows." }

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\\..")).Path
Set-Location $ProjectRoot
$Python = Join-Path $ProjectRoot ".venv\\Scripts\\python.exe"
if (-not (Test-Path $Python)) { throw "Run .\\setup-windows.ps1 first." }

& $Python -m pip install -r requirements-build-windows.txt
if ($LASTEXITCODE -ne 0) { throw "Failed to install Windows build dependencies." }
& $Python -m PyInstaller --noconfirm --clean --distpath dist --workpath build/windows packaging/windows/Whispered.windows.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

$Exe = Join-Path $ProjectRoot "dist\\Whispered\\Whispered.exe"
& $Exe --smoke-test
if ($LASTEXITCODE -ne 0) { throw "Frozen smoke test failed." }

$Zip = Join-Path $ProjectRoot "dist\\Whispered-windows-x64.zip"
if (Test-Path $Zip) { Remove-Item -Force $Zip }
Compress-Archive -Path (Join-Path $ProjectRoot "dist\\Whispered\\*") -DestinationPath $Zip

if (-not $SkipInstaller) {
    $Iscc = Get-Command iscc -ErrorAction SilentlyContinue
    if ($Iscc) {
        & $Iscc.Source packaging/windows/installer.iss
    } else {
        Write-Warning "Inno Setup (iscc) not found; ZIP was built, installer was skipped."
    }
}
