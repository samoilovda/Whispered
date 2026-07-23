[CmdletBinding()]
param(
    [string]$PythonExecutable = "py",
    [string]$PythonVersion = "-3.11"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

if (-not [Environment]::Is64BitOperatingSystem) {
    throw "Whispered for Windows requires 64-bit Windows."
}

$PythonArgs = if ($PythonVersion) { @($PythonVersion) } else { @() }
try {
    & $PythonExecutable @PythonArgs --version | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Python version probe failed." }
} catch {
    throw "Python 3.11 x64 was not found. Install it, then run: .\\setup-windows.ps1"
}

if (-not (Test-Path ".venv")) {
    & $PythonExecutable @PythonArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Failed to create .venv." }
}

$Python = Join-Path $ProjectRoot ".venv\\Scripts\\python.exe"
& $Python -m pip install --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) { throw "Failed to upgrade pip tooling." }
& $Python -m pip install -r requirements-windows.lock
if ($LASTEXITCODE -ne 0) { throw "Failed to install Windows runtime dependencies." }

& $Python -c "import PyQt6, sounddevice, docx; from pywhispercpp.model import Model; print('Runtime dependencies: OK')"
if ($LASTEXITCODE -ne 0) { throw "Windows runtime import check failed." }

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Warning "FFmpeg was not found. WAV transcription works; video/audio conversion and Cut need FFmpeg on PATH."
}

Write-Host "Setup complete. Run: .\\run-windows.ps1" -ForegroundColor Green
