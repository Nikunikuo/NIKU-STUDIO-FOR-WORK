[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [ValidateRange(10, 600)]
    [int]$StartupTimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$StoryRoot = Split-Path -Parent $PSScriptRoot
$ProjectHub = Split-Path -Parent $StoryRoot
# Keep this comment as the human-readable product boundary.  Windows
# PowerShell 5.1 can decode a UTF-8-no-BOM script's Japanese string literal
# incorrectly, so resolve the sibling checkout from its stable ASCII suffix
# instead of embedding the non-ASCII directory name in an executable value.
# Existing H3 checkout: 動画生成モデルH3_OSS
$H3Root = @(
    Get-ChildItem -LiteralPath $ProjectHub -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match "H3_OSS$" }
)[0].FullName
if ([string]::IsNullOrWhiteSpace($H3Root) -or -not (Test-Path -LiteralPath $H3Root -PathType Container)) {
    throw "Existing H3 Studio checkout was not found beneath: $ProjectHub"
}
$H3Launcher = Join-Path $H3Root "scripts\start_webui.ps1"
$LogRoot = Join-Path $StoryRoot "work\logs"
$LaunchStamp = Get-Date -Format "yyyyMMdd-HHmmss"

$H3Url = "http://127.0.0.1:7863"
$SidecarUrl = "http://127.0.0.1:7864"
$StoryUrl = "http://localhost:3000"

New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null

function Test-LoopbackListener {
    param([Parameter(Mandatory = $true)][int]$Port)

    try {
        return @(
            Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop |
                Where-Object { $_.LocalAddress -in @("127.0.0.1", "::1", "0.0.0.0", "::") }
        ).Count -gt 0
    } catch {
        # Get-NetTCPConnection can be unavailable in restricted PowerShell hosts.
        # A short TCP connect is a read-only fallback and never starts a process.
        $client = [System.Net.Sockets.TcpClient]::new()
        try {
            $connect = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
            if (-not $connect.AsyncWaitHandle.WaitOne(250)) {
                return $false
            }
            $client.EndConnect($connect)
            return $true
        } catch {
            return $false
        } finally {
            $client.Dispose()
        }
    }
}

function Get-LocalJson {
    param([Parameter(Mandatory = $true)][string]$Uri)

    return Invoke-RestMethod -Method Get -Uri $Uri -TimeoutSec 3
}

function Test-H3Studio {
    try {
        $state = Get-LocalJson "$H3Url/api/state"
        return (
            $null -ne $state.capabilities -and
            [string]$state.capabilities.backend -eq "comfy" -and
            [bool]$state.capabilities.ready
        )
    } catch {
        return $false
    }
}

function Test-StorySidecar {
    try {
        $health = Get-LocalJson "$SidecarUrl/api/health"
        return (
            [string]$health.status -eq "ok" -and
            [string]$health.projectCore -eq "ready" -and
            [string]$health.h3 -in @("ready", "busy")
        )
    } catch {
        return $false
    }
}

function Test-StoryWebUi {
    try {
        $response = Invoke-WebRequest -Method Get -Uri $StoryUrl -UseBasicParsing -TimeoutSec 5
        return (
            [int]$response.StatusCode -eq 200 -and
            # The product was renamed to NIKU STDUIO FOR WORK, but older
            # builds still expose the original title. Accept both markers so
            # an already-running compatible Web UI is reused safely.
            [string]$response.Content -match "NIKU STDUIO FOR WORK|H3 Story Studio"
        )
    } catch {
        return $false
    }
}

function Wait-ForService {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][scriptblock]$Probe,
        [System.Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)][string]$StandardOutputLog,
        [Parameter(Mandatory = $true)][string]$StandardErrorLog
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($StartupTimeoutSeconds)
    do {
        if (& $Probe) {
            return
        }
        if ($null -ne $Process -and $Process.HasExited) {
            throw "$Name exited before becoming ready (exit $($Process.ExitCode)). Logs: $StandardOutputLog ; $StandardErrorLog"
        }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)

    throw "$Name did not become ready within $StartupTimeoutSeconds seconds. Logs: $StandardOutputLog ; $StandardErrorLog"
}

function Assert-PortAvailableForStart {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$Name
    )

    if (Test-LoopbackListener $Port) {
        throw "Port $Port is already occupied, but it is not a compatible $Name. No process was started or stopped."
    }
}

function Start-H3StudioIfNeeded {
    if (Test-H3Studio) {
        Write-Host "[ready] H3 Studio already running: $H3Url"
        return
    }

    Assert-PortAvailableForStart -Port 7863 -Name "H3 Studio"
    if (-not (Test-Path -LiteralPath $H3Launcher -PathType Leaf)) {
        throw "Existing H3 Studio launcher was not found: $H3Launcher"
    }

    $stdout = Join-Path $LogRoot "h3-$LaunchStamp.stdout.log"
    $stderr = Join-Path $LogRoot "h3-$LaunchStamp.stderr.log"
    $arguments = @(
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $H3Launcher,
        "-Port", "7863"
    )
    # The existing H3 launcher passes --open to its server.  Override Python's
    # browser helper only in this child so Story Studio remains the single UI
    # opened at the end of this orchestrated startup (and -NoBrowser stays
    # browser-free).  where.exe is a harmless console program which simply
    # rejects the URL argument supplied by Python's webbrowser module.
    $previousBrowser = [Environment]::GetEnvironmentVariable("BROWSER", "Process")
    try {
        $env:BROWSER = Join-Path $env:SystemRoot "System32\where.exe"
        $process = Start-Process -FilePath "powershell.exe" -ArgumentList $arguments `
            -WorkingDirectory $H3Root -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    } finally {
        if ($null -eq $previousBrowser) {
            [Environment]::SetEnvironmentVariable("BROWSER", $null, "Process")
        } else {
            $env:BROWSER = $previousBrowser
        }
    }
    Write-Host "[start] H3 Studio (PID $($process.Id)); logs: $stdout"
    Wait-ForService -Name "H3 Studio" -Probe ${function:Test-H3Studio} -Process $process `
        -StandardOutputLog $stdout -StandardErrorLog $stderr
    Write-Host "[ready] H3 Studio: $H3Url"
}

function Start-StorySidecarIfNeeded {
    if (Test-StorySidecar) {
        Write-Host "[ready] Story sidecar already running: $SidecarUrl"
        return
    }

    Assert-PortAvailableForStart -Port 7864 -Name "H3 Story Studio sidecar"
    $pythonCommand = Get-Command "python.exe" -ErrorAction SilentlyContinue
    if ($null -eq $pythonCommand) {
        throw "python.exe was not found on PATH. Python 3 is required for the Story sidecar."
    }

    $stdout = Join-Path $LogRoot "sidecar-$LaunchStamp.stdout.log"
    $stderr = Join-Path $LogRoot "sidecar-$LaunchStamp.stderr.log"
    $process = Start-Process -FilePath $pythonCommand.Source -ArgumentList @(
        "-B", "-u", "-m", "sidecar.http_server"
    ) -WorkingDirectory $StoryRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    Write-Host "[start] Story sidecar (PID $($process.Id)); logs: $stdout"
    Wait-ForService -Name "H3 Story Studio sidecar" -Probe ${function:Test-StorySidecar} -Process $process `
        -StandardOutputLog $stdout -StandardErrorLog $stderr
    Write-Host "[ready] Story sidecar: $SidecarUrl"
}

function Start-StoryWebUiIfNeeded {
    if (Test-StoryWebUi) {
        Write-Host "[ready] Story Web UI already running: $StoryUrl"
        return
    }

    Assert-PortAvailableForStart -Port 3000 -Name "H3 Story Studio Web UI"
    $npmCommand = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
    if ($null -eq $npmCommand) {
        throw "npm.cmd was not found on PATH. Node.js 22.13 or newer is required."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $StoryRoot "node_modules") -PathType Container)) {
        throw "Node dependencies are missing. Run 'npm install' in $StoryRoot first."
    }

    $stdout = Join-Path $LogRoot "web-$LaunchStamp.stdout.log"
    $stderr = Join-Path $LogRoot "web-$LaunchStamp.stderr.log"
    $process = Start-Process -FilePath $npmCommand.Source -ArgumentList @(
        "run", "dev", "--", "--port", "3000", "--hostname", "localhost"
    ) -WorkingDirectory $StoryRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    Write-Host "[start] Story Web UI (PID $($process.Id)); logs: $stdout"
    Wait-ForService -Name "H3 Story Studio Web UI" -Probe ${function:Test-StoryWebUi} -Process $process `
        -StandardOutputLog $stdout -StandardErrorLog $stderr
    Write-Host "[ready] Story Web UI: $StoryUrl"
}

Write-Host "Starting H3 Story Studio from: $StoryRoot"
Start-H3StudioIfNeeded
Start-StorySidecarIfNeeded
Start-StoryWebUiIfNeeded

if (-not $NoBrowser) {
    Start-Process -FilePath $StoryUrl | Out-Null
    Write-Host "[open] $StoryUrl"
} else {
    Write-Host "[done] All services are healthy. Browser launch was skipped (-NoBrowser)."
}
