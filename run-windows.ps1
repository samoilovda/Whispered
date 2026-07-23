[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv\\Scripts\\python.exe"

if (-not (Test-Path $Python)) {
    throw "Virtual environment not found. Run .\\setup-windows.ps1 first."
}

& $Python (Join-Path $ProjectRoot "main.py")
exit $LASTEXITCODE
