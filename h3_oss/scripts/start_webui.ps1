param(
    [int]$Port = 7863
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$ComfySetup = Join-Path $PSScriptRoot "setup_comfy.ps1"
$DesiredAttentionBackend = if ([string]::IsNullOrWhiteSpace($env:H3_ATTENTION_BACKEND)) {
    "sage"
} else {
    $env:H3_ATTENTION_BACKEND.Trim().ToLowerInvariant()
}
if ($DesiredAttentionBackend -notin @("sage", "pytorch")) {
    throw "H3_ATTENTION_BACKEND must be 'sage' or 'pytorch'."
}

if (-not (Test-Path -LiteralPath $Python)) {
    throw "NIKU H STUDIO is not set up yet. Double-click Setup-H3-Studio.cmd first."
}

if (-not (Test-Path -LiteralPath $ComfySetup)) {
    throw "ComfyUI setup verifier is missing: $ComfySetup"
}

# This is intentionally a fast verification: fixed source revisions, the
# isolated runtime, CUDA availability, and exact byte sizes for the H3 and Qwen
# planner files. Full SHA-256 validation and the Qwen full-weight load probe are
# available through setup_comfy.ps1 but would make every normal launch slow.
& $ComfySetup -VerifyOnly -SkipModelHash
if ($LASTEXITCODE -ne 0) {
    throw "ComfyUI verification failed. Rerun Setup-H3-Studio.cmd to repair or resume the local setup."
}

Set-Location -LiteralPath $Root
$Url = "http://127.0.0.1:$Port"
$ExistingState = $null
try {
    $ExistingState = Invoke-RestMethod -Uri "$Url/api/state" -TimeoutSec 2
} catch {
    # No healthy local server is listening yet; start it below.
}

if ($null -ne $ExistingState) {
    if ($ExistingState.capabilities.backend -ne "comfy" -or -not $ExistingState.capabilities.ready) {
        throw "Port $Port already has an incompatible or incomplete NIKU H STUDIO. Stop that process, then launch again."
    }
    $RunningAttentionBackend = [string]$ExistingState.capabilities.attention.backend
    if ($RunningAttentionBackend -ne $DesiredAttentionBackend) {
        throw "NIKU H STUDIO is already running with attention backend '$RunningAttentionBackend', but '$DesiredAttentionBackend' was requested. Stop the existing NIKU H STUDIO with Ctrl+C, then launch again."
    }
    Start-Process -FilePath $Url
    Write-Host "NIKU H STUDIO is already running: $Url"
    return
}

try {
    $Host.UI.RawUI.WindowTitle = "NIKU H STUDIO - keep this window open"
} catch {
    # Some non-interactive PowerShell hosts do not expose RawUI.
}
# FastAPI remains the only public-facing local process. Its JobManager starts
# one private ComfyUI child on a dynamically selected loopback port per active
# engine lifecycle, so there is no fixed :8188 service to collide with or leave
# behind after cancellation.
& $Python -m webui.server --port $Port --open
