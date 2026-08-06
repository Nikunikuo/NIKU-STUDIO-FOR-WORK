param(
    [string]$PythonExe = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "python_runtime.ps1")

$PythonExe = Resolve-H3Python312 -Requested $PythonExe
$Venv = Join-Path $Root ".venv"
$VenvPython = Join-Path $Venv "Scripts\python.exe"
$WebRequirements = Join-Path $Root "requirements.webui.txt"

Write-Host "Using Python 3.12: $PythonExe"
if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    & $PythonExe -m venv $Venv
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the isolated Web UI environment: $Venv"
    }
}
if (-not (Test-H3Python312 -Path $VenvPython)) {
    throw "The existing .venv is not a 64-bit Python 3.12 environment. Rename or remove '$Venv', then rerun setup."
}

& $VenvPython -m pip install --upgrade pip==26.2
if ($LASTEXITCODE -ne 0) {
    throw "Web UI pip bootstrap failed."
}
& $VenvPython -m pip install --requirement $WebRequirements
if ($LASTEXITCODE -ne 0) {
    throw "Web UI dependency installation failed."
}
& $VenvPython -c "import av, fastapi, multipart, psutil, uvicorn; print('webui python', __import__('sys').version.split()[0]); print('av', av.__version__); print('fastapi', fastapi.__version__); print('uvicorn', uvicorn.__version__)"
if ($LASTEXITCODE -ne 0) {
    throw "Web UI runtime validation failed."
}

Write-Host "NIKU H STUDIO Web UI environment is ready."
