Set-StrictMode -Version Latest

function Resolve-H3CommandPath {
    param([Parameter(Mandatory = $true)][string]$Candidate)

    if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
        return (Resolve-Path -LiteralPath $Candidate).Path
    }
    $command = Get-Command $Candidate -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -ne $command) {
        return $command.Source
    }
    return $null
}

function Test-H3Python312 {
    param([Parameter(Mandatory = $true)][string]$Path)

    try {
        $probe = @(& $Path -c "import struct, sys; print(f'{sys.version_info.major}.{sys.version_info.minor}|{struct.calcsize('P') * 8}')" 2>$null)
        if ($LASTEXITCODE -ne 0 -or $probe.Count -eq 0) {
            return $false
        }
        return $probe[-1].Trim() -eq "3.12|64"
    } catch {
        return $false
    }
}

function Resolve-H3Python312 {
    param([string]$Requested = "")

    $candidates = [System.Collections.Generic.List[string]]::new()
    if (-not [string]::IsNullOrWhiteSpace($Requested)) {
        $candidates.Add($Requested.Trim())
    }
    if (-not [string]::IsNullOrWhiteSpace($env:H3_PYTHON)) {
        $candidates.Add($env:H3_PYTHON.Trim())
    }

    $launcher = Resolve-H3CommandPath -Candidate "py.exe"
    if ($null -ne $launcher) {
        try {
            $launched = @(& $launcher -3.12 -c "import sys; print(sys.executable)" 2>$null)
            if ($LASTEXITCODE -eq 0 -and $launched.Count -gt 0) {
                $candidates.Add($launched[-1].Trim())
            }
        } catch {
            # Continue through the remaining discovery methods.
        }
    }

    foreach ($name in @("python3.12.exe", "python3.12", "python.exe", "python")) {
        $resolved = Resolve-H3CommandPath -Candidate $name
        if ($null -ne $resolved) {
            $candidates.Add($resolved)
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        $candidates.Add((Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"))
    }

    $seen = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            continue
        }
        $path = Resolve-H3CommandPath -Candidate $candidate
        if ($null -eq $path -or -not $seen.Add($path)) {
            continue
        }
        if (Test-H3Python312 -Path $path) {
            return $path
        }
    }

    throw @"
64-bit Python 3.12 was not found.

Install 64-bit Python 3.12 (`winget install --exact --id Python.Python.3.12`)
or use https://www.python.org/downloads/release/python-31210/ and enable the
Python launcher (py.exe), then rerun Setup-H3-Studio.cmd.
Python 3.13 is not supported by this pinned runtime.

Advanced: set H3_PYTHON or pass -PythonExe with the full python.exe path.
"@
}

function Assert-H3Command {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$InstallHint
    )

    if ($null -eq (Resolve-H3CommandPath -Candidate $Name)) {
        throw "$Name was not found. $InstallHint"
    }
}
