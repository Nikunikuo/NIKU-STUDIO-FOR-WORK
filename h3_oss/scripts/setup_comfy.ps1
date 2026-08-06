param(
    [string]$PythonExe = "",
    [switch]$VerifyOnly,
    [switch]$SkipModelHash,
    [switch]$AcceptMiniMaxH3License
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "python_runtime.ps1")
Assert-H3Command -Name "git.exe" -InstallHint "Install Git for Windows from https://git-scm.com/download/win and reopen this terminal."

$AttentionBackend = if ([string]::IsNullOrWhiteSpace($env:H3_ATTENTION_BACKEND)) {
    "sage"
} else {
    $env:H3_ATTENTION_BACKEND.Trim().ToLowerInvariant()
}
if ($AttentionBackend -notin @("sage", "pytorch")) {
    throw "H3_ATTENTION_BACKEND must be 'sage' or 'pytorch'."
}

$ComfyVenv = Join-Path $Root ".comfy-venv"
$ComfyPython = Join-Path $ComfyVenv "Scripts\python.exe"
$ComfySource = Join-Path $Root ".upstream\ComfyUI"
$Requirements = Join-Path $Root "requirements.comfy.txt"
$ModelLockPath = Join-Path $Root "comfy_models.lock.json"
$PromptPlannerLockPath = Join-Path $Root "prompt_planner.lock.json"
$PromptPlannerRoot = Join-Path $Root "models\prompt_planner\Qwen3-4B-Instruct-2507"
$PromptPlannerProvenancePath = Join-Path $PromptPlannerRoot "h3-studio-provenance.json"
$ComfySha = "14b05228cef127ce529bc0c08660770d4af3e9a8"
$SageVersion = "2.2.0+cu130torch2.10.0andhigher.post6"
$SageWheelName = "sageattention-2.2.0+cu130torch2.10.0andhigher.post6-cp310-abi3-win_amd64.whl"
$SageWheelUrl = "https://github.com/woct0rdho/SageAttention/releases/download/v2.2.0-windows.post6/sageattention-2.2.0%2Bcu130torch2.10.0andhigher.post6-cp310-abi3-win_amd64.whl"
$SageWheelBytes = [int64]16656067
$SageWheelSha256 = "1635283f5c01ec3cda58a784d0d7eabbcaffaf9511d1b263db4750e1ed7958bb"

function Test-FixedCheckout {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ExpectedSha,
        [Parameter(Mandatory = $true)][string]$Name
    )

    if (-not (Test-Path -LiteralPath (Join-Path $Path ".git"))) {
        throw "$Name checkout is missing: $Path"
    }
    $actual = (& git -C $Path rev-parse HEAD | Select-Object -Last 1).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Could not read the $Name checkout revision."
    }
    if ($actual -ne $ExpectedSha) {
        throw "$Name SHA mismatch: expected $ExpectedSha, got $actual"
    }
    $changes = @(& git -C $Path status --porcelain --untracked-files=all)
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect the $Name checkout."
    }
    if ($changes.Count -gt 0) {
        throw "$Name checkout has local or untracked files: $Path"
    }
}

function Set-FixedCheckout {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Repository,
        [Parameter(Mandatory = $true)][string]$ExpectedSha,
        [Parameter(Mandatory = $true)][string]$Name
    )

    $gitDirectory = Join-Path $Path ".git"
    $isNewCheckout = $false
    if (-not (Test-Path -LiteralPath $gitDirectory)) {
        if (Test-Path -LiteralPath $Path) {
            $existing = @(Get-ChildItem -LiteralPath $Path -Force)
            if ($existing.Count -gt 0) {
                throw "$Name target exists but is not a Git checkout: $Path"
            }
        } else {
            New-Item -ItemType Directory -Path (Split-Path -Parent $Path) -Force | Out-Null
        }
        & git clone --filter=blob:none --no-checkout $Repository $Path
        if ($LASTEXITCODE -ne 0) {
            throw "$Name clone failed."
        }
        $isNewCheckout = $true
    }

    if (-not $isNewCheckout) {
        $changes = @(& git -C $Path status --porcelain --untracked-files=all)
        if ($LASTEXITCODE -ne 0) {
            throw "Could not inspect the $Name checkout."
        }
        if ($changes.Count -gt 0) {
            throw "$Name checkout has local changes. Preserve or remove them before changing revisions: $Path"
        }
    }

    & git -C $Path fetch --depth 1 origin $ExpectedSha
    if ($LASTEXITCODE -ne 0) {
        throw "$Name fetch failed for $ExpectedSha."
    }
    & git -C $Path checkout --detach $ExpectedSha
    if ($LASTEXITCODE -ne 0) {
        throw "$Name checkout failed for $ExpectedSha."
    }
    Test-FixedCheckout -Path $Path -ExpectedSha $ExpectedSha -Name $Name
}

function Read-ModelLock {
    if (-not (Test-Path -LiteralPath $ModelLockPath)) {
        throw "Comfy model lock is missing: $ModelLockPath"
    }
    return Get-Content -LiteralPath $ModelLockPath -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Read-PromptPlannerLock {
    if (-not (Test-Path -LiteralPath $PromptPlannerLockPath)) {
        throw "Prompt planner model lock is missing: $PromptPlannerLockPath"
    }
    return Get-Content -LiteralPath $PromptPlannerLockPath -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Test-ComfyModels {
    param(
        [Parameter(Mandatory = $true)]$Lock,
        [switch]$FullHash
    )

    $total = [int64]0
    foreach ($entry in $Lock.files) {
        $path = Join-Path $Root ([string]$entry.path)
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Comfy model file is missing: $path"
        }
        $item = Get-Item -LiteralPath $path
        $expectedSize = [int64]$entry.size
        if ($item.Length -ne $expectedSize) {
            throw "Comfy model size mismatch for $path`: expected $expectedSize, got $($item.Length)"
        }
        $total += $item.Length

        if ($FullHash) {
            Write-Host "SHA-256: $($entry.path)"
            $actualHash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
            $expectedHash = ([string]$entry.sha256).ToLowerInvariant()
            if ($actualHash -ne $expectedHash) {
                throw "Comfy model SHA-256 mismatch for $path`: expected $expectedHash, got $actualHash. Preserve it for inspection, then remove or replace it before rerunning setup."
            }
        }
    }

    $expectedTotal = [int64]$Lock.verification.total_bytes
    if ($total -ne $expectedTotal) {
        throw "Comfy model total size mismatch: expected $expectedTotal, got $total"
    }
    Write-Host "Comfy models verified: $($Lock.files.Count) files, $total bytes$(if ($FullHash) { ', SHA-256 matched' } else { ', sizes matched' })."
}

function Get-MissingModelBytes {
    param([Parameter(Mandatory = $true)]$Lock)

    $missingBytes = [int64]0
    foreach ($entry in $Lock.files) {
        $path = Join-Path $Root ([string]$entry.path)
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $actualSize = (Get-Item -LiteralPath $path).Length
            if ($actualSize -ne [int64]$entry.size) {
                throw "An existing Comfy model has the wrong size: $path. Preserve it for inspection, then remove or replace it before rerunning setup."
            }
        } else {
            $missingBytes += [int64]$entry.size
        }
    }
    return $missingBytes
}

function Test-PromptPlannerFiles {
    param(
        [Parameter(Mandatory = $true)]$Lock,
        [switch]$FullHash
    )

    $total = [int64]0
    $expectedFiles = @($Lock.files)
    foreach ($entry in $expectedFiles) {
        $path = Join-Path $Root ([string]$entry.path)
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Required Qwen prompt planner file is missing: $path. Rerun Setup-H3-Studio.cmd to repair or resume the fixed-revision download."
        }
        $item = Get-Item -LiteralPath $path
        $expectedSize = [int64]$entry.size
        if ($item.Length -ne $expectedSize) {
            throw "Qwen prompt planner size mismatch for $path`: expected $expectedSize, got $($item.Length). Preserve it for inspection, then remove or replace it before rerunning setup."
        }
        $total += $item.Length

        if ($FullHash) {
            Write-Host "SHA-256: $($entry.path)"
            $actualHash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
            $expectedHash = ([string]$entry.sha256).ToLowerInvariant()
            if ($actualHash -ne $expectedHash) {
                throw "Qwen prompt planner SHA-256 mismatch for $path`: expected $expectedHash, got $actualHash. Preserve it for inspection, then remove or replace it before rerunning setup."
            }
        }
    }

    $expectedTotal = [int64]$Lock.verification.total_bytes
    if ($total -ne $expectedTotal) {
        throw "Qwen prompt planner total size mismatch: expected $expectedTotal, got $total"
    }
    Write-Host "Qwen prompt planner verified: $($expectedFiles.Count) files, $total bytes$(if ($FullHash) { ', SHA-256 matched' } else { ', sizes matched' })."
}

function Get-PromptPlannerProvenance {
    param([Parameter(Mandatory = $true)]$Lock)

    $lockSha256 = (Get-FileHash -LiteralPath $PromptPlannerLockPath -Algorithm SHA256).Hash.ToLowerInvariant()
    return [pscustomobject][ordered]@{
        schema_version = 1
        model_id = [string]$Lock.source.repo_id
        revision = [string]$Lock.source.revision
        lock_sha256 = $lockSha256
        file_count = @($Lock.files).Count
        total_bytes = [int64]$Lock.verification.total_bytes
    }
}

function Test-PromptPlannerProvenance {
    param([Parameter(Mandatory = $true)]$Lock)

    $repair = "Run Setup-H3-Studio.cmd normally (without -VerifyOnly) to revalidate and repair it."
    if (-not (Test-Path -LiteralPath $PromptPlannerProvenancePath -PathType Leaf)) {
        throw "Qwen prompt planner provenance marker is missing: $PromptPlannerProvenancePath. $repair"
    }

    try {
        $marker = Get-Content -LiteralPath $PromptPlannerProvenancePath -Raw -Encoding UTF8 | ConvertFrom-Json
        $expected = Get-PromptPlannerProvenance -Lock $Lock
        $expectedNames = @($expected.PSObject.Properties.Name)
        $actualNames = @($marker.PSObject.Properties.Name)
        $differentNames = @(Compare-Object -ReferenceObject $expectedNames -DifferenceObject $actualNames)
        $valid = (
            $differentNames.Count -eq 0 -and
            $marker.schema_version -is [int] -and
            $marker.file_count -is [int] -and
            $marker.total_bytes -is [long] -and
            $marker.schema_version -eq $expected.schema_version -and
            $marker.model_id -ceq $expected.model_id -and
            $marker.revision -ceq $expected.revision -and
            $marker.lock_sha256 -ceq $expected.lock_sha256 -and
            $marker.file_count -eq $expected.file_count -and
            $marker.total_bytes -eq $expected.total_bytes
        )
    } catch {
        throw "Qwen prompt planner provenance marker is invalid: $PromptPlannerProvenancePath. $repair Detail: $($_.Exception.Message)"
    }
    if (-not $valid) {
        throw "Qwen prompt planner provenance marker does not match the current model lock: $PromptPlannerProvenancePath. $repair"
    }
    Write-Host "Qwen prompt planner provenance verified: revision $($expected.revision), lock SHA-256 $($expected.lock_sha256)."
}

function Write-PromptPlannerProvenance {
    param([Parameter(Mandatory = $true)]$Lock)

    # This marker is NIKU H STUDIO-owned provenance, not a Hugging Face cache file.
    # Call this function only after all nine files passed their pinned SHA-256
    # checks during a normal (non-VerifyOnly) setup invocation.
    $marker = Get-PromptPlannerProvenance -Lock $Lock
    $json = ($marker | ConvertTo-Json -Depth 3) + "`n"
    $temporaryPath = "$PromptPlannerProvenancePath.tmp-$PID-$([Guid]::NewGuid().ToString('N'))"
    $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    try {
        [System.IO.File]::WriteAllText($temporaryPath, $json, $utf8WithoutBom)
        if (Test-Path -LiteralPath $PromptPlannerProvenancePath -PathType Leaf) {
            [System.IO.File]::Replace($temporaryPath, $PromptPlannerProvenancePath, $null)
        } else {
            [System.IO.File]::Move($temporaryPath, $PromptPlannerProvenancePath)
        }
    } finally {
        Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
    }
    Write-Host "Qwen prompt planner provenance marker written after full SHA-256 verification."
}

function Get-PromptPlannerInventory {
    param([Parameter(Mandatory = $true)]$Lock)

    $missingBytes = [int64]0
    $presentFiles = 0
    $issues = [System.Collections.Generic.List[string]]::new()
    $expectedFiles = @($Lock.files)
    foreach ($entry in $expectedFiles) {
        $path = Join-Path $Root ([string]$entry.path)
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $presentFiles++
            $actualSize = (Get-Item -LiteralPath $path).Length
            if ($actualSize -ne [int64]$entry.size) {
                $issues.Add("Wrong byte size: $path (expected $($entry.size), got $actualSize)")
            }
        } elseif (Test-Path -LiteralPath $path) {
            $presentFiles++
            $issues.Add("Expected a regular file but found another filesystem object: $path")
        } else {
            $missingBytes += [int64]$entry.size
        }
    }

    $status = if ($presentFiles -eq 0) {
        "absent"
    } elseif ($presentFiles -eq $expectedFiles.Count -and $issues.Count -eq 0) {
        "complete"
    } else {
        "incomplete"
    }

    return [pscustomobject]@{
        Status = $status
        ExpectedFileCount = $expectedFiles.Count
        PresentFileCount = $presentFiles
        MissingBytes = $missingBytes
        Issues = @($issues)
    }
}

function Assert-H3NvidiaPreflight {
    Assert-H3Command -Name "nvidia-smi.exe" -InstallHint "Install or update the NVIDIA display driver, then reboot."
    $rows = @(& nvidia-smi.exe --id=0 --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits 2>&1)
    if ($LASTEXITCODE -ne 0 -or $rows.Count -eq 0) {
        throw "nvidia-smi could not inspect CUDA GPU 0. Install or update the NVIDIA display driver, then reboot."
    }
    $parts = @($rows[-1] -split ',' | ForEach-Object { $_.Trim() })
    if ($parts.Count -lt 3) {
        throw "Unexpected nvidia-smi response: $($rows[-1])"
    }
    $memoryMiB = [int64]0
    if (-not [int64]::TryParse($parts[1], [ref]$memoryMiB)) {
        throw "Could not parse GPU memory from nvidia-smi: $($rows[-1])"
    }
    if ($memoryMiB -lt 30720) {
        throw "CUDA GPU 0 requires at least 30 GiB VRAM; detected $memoryMiB MiB on $($parts[0])."
    }
    Write-Host "GPU preflight: $($parts[0]), $memoryMiB MiB VRAM, NVIDIA driver $($parts[2])."
}

function Test-ComfyRuntime {
    if (-not (Test-Path -LiteralPath $ComfyPython -PathType Leaf)) {
        throw "ComfyUI Python environment is missing: $ComfyPython"
    }
    $runtimeProbe = @'
import importlib.metadata
import pathlib
import sys

import torch
import torchaudio
import torchao
import torchvision

root = pathlib.Path(sys.argv[1])
attention_backend = sys.argv[2]
sys.path.insert(0, str(root))
import comfy

assert tuple(sys.version_info[:2]) == (3, 12), sys.version
assert torch.__version__ == '2.13.0+cu130', torch.__version__
assert torchvision.__version__ == '0.28.0+cu130', torchvision.__version__
assert torchaudio.__version__ == '2.11.0+cu130', torchaudio.__version__
assert torchao.__version__ == '0.17.0', torchao.__version__
assert torch.version.cuda == '13.0', torch.version.cuda
assert torch.cuda.is_available()
gpu_memory_gib = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
assert gpu_memory_gib >= 30.0, f'{torch.cuda.get_device_name(0)} has only {gpu_memory_gib:.1f} GiB VRAM'

probe = torch.zeros((1, 2, 64), dtype=torch.float32)
resampled = torchaudio.functional.resample(probe, 16000, 32000)
assert resampled.shape == (1, 2, 128), resampled.shape

if attention_backend == 'sage':
    from sageattention import sageattn

    assert importlib.metadata.version('sageattention') == '2.2.0+cu130torch2.10.0andhigher.post6'
    assert importlib.metadata.version('triton-windows') == '3.7.1.post27'
    q = torch.randn((1, 128, 4, 64), device='cuda', dtype=torch.float16)
    sage_output = sageattn(q, q, q, tensor_layout='NHD')
    assert sage_output.shape == q.shape
    assert torch.isfinite(sage_output).all()

print('python', sys.version.split()[0])
print('torch', torch.__version__)
print('torchvision', torchvision.__version__)
print('torchaudio', torchaudio.__version__)
print('torchao', torchao.__version__)
print('attention_backend', attention_backend)
if attention_backend == 'sage':
    print('sageattention', importlib.metadata.version('sageattention'))
    print('triton-windows', importlib.metadata.version('triton-windows'))
print('cuda', torch.version.cuda)
print('gpu', torch.cuda.get_device_name(0))
print('gpu_memory_gib', f'{gpu_memory_gib:.1f}')
print('comfy_source', root)
print('audio_resample', 'ok')
print('sageattention_kernel', 'ok' if attention_backend == 'sage' else 'skipped')
'@
    & $ComfyPython -c $runtimeProbe $ComfySource $AttentionBackend
    if ($LASTEXITCODE -ne 0) {
        throw "ComfyUI runtime validation failed."
    }
}

function Test-PromptPlannerRuntime {
    param([switch]$LoadWeights)

    if (-not (Test-Path -LiteralPath $ComfyPython -PathType Leaf)) {
        throw "ComfyUI Python environment is missing: $ComfyPython"
    }

    $runtimeProbe = @'
import os
import pathlib
import sys

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'

import torch
from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer, Qwen3ForCausalLM

model_root = pathlib.Path(sys.argv[1]).resolve()
load_weights = sys.argv[2] == '1'
config = AutoConfig.from_pretrained(
    model_root,
    local_files_only=True,
    trust_remote_code=False,
)
assert config.model_type == 'qwen3', config.model_type
assert list(config.architectures or []) == ['Qwen3ForCausalLM'], config.architectures
assert Qwen3ForCausalLM.config_class.model_type == 'qwen3'

tokenizer = AutoTokenizer.from_pretrained(
    model_root,
    local_files_only=True,
    trust_remote_code=False,
    use_fast=True,
)
rendered = tokenizer.apply_chat_template(
    [
        {'role': 'system', 'content': 'Return strict JSON only.'},
        {'role': 'user', 'content': '\u65e5\u672c\u8a9e\u306e\u6620\u50cf\u6307\u793a'},
    ],
    tokenize=False,
    add_generation_prompt=True,
)
for required in ('Return strict JSON only.', '\u65e5\u672c\u8a9e\u306e\u6620\u50cf\u6307\u793a'):
    assert required in rendered, rendered

print('prompt_planner_model_type', config.model_type)
print('prompt_planner_architecture', config.architectures[0])
print('prompt_planner_tokenizer', tokenizer.__class__.__name__)
print('prompt_planner_chat_template', 'ok')

if load_weights:
    model = AutoModelForCausalLM.from_pretrained(
        model_root,
        local_files_only=True,
        trust_remote_code=False,
        dtype=torch.bfloat16,
        device_map='cpu',
        low_cpu_mem_usage=True,
    )
    parameter_count = sum(parameter.numel() for parameter in model.parameters())
    assert model.__class__.__name__ == 'Qwen3ForCausalLM', model.__class__.__name__
    assert parameter_count == 4_022_468_096, parameter_count
    assert next(model.parameters()).dtype == torch.bfloat16
    print('prompt_planner_parameter_count', parameter_count)
    print('prompt_planner_offline_weight_load', 'ok')
else:
    print('prompt_planner_offline_weight_load', 'skipped-fast-launch-check')
'@
    & $ComfyPython -c $runtimeProbe $PromptPlannerRoot $(if ($LoadWeights) { "1" } else { "0" })
    if ($LASTEXITCODE -ne 0) {
        throw "Required Qwen prompt planner runtime validation failed. Rerun Setup-H3-Studio.cmd to repair its pinned files and isolated runtime."
    }
}

$modelLock = Read-ModelLock
$promptPlannerLock = Read-PromptPlannerLock
$missingBytes = Get-MissingModelBytes -Lock $modelLock
$promptPlannerInventory = Get-PromptPlannerInventory -Lock $promptPlannerLock
$promptPlannerMissingBytes = [int64]0

if ($promptPlannerInventory.Status -eq "absent") {
    if ($VerifyOnly) {
        throw "The required Qwen prompt planner is not installed. Rerun Setup-H3-Studio.cmd before starting NIKU H STUDIO."
    }
    $promptPlannerMissingBytes = [int64]$promptPlannerLock.verification.total_bytes
} elseif ($promptPlannerInventory.Status -eq "incomplete") {
    $inventorySummary = "Detected $($promptPlannerInventory.PresentFileCount) of $($promptPlannerInventory.ExpectedFileCount) pinned Qwen prompt planner files."
    $issueDetails = if ($promptPlannerInventory.Issues.Count -gt 0) {
        "`n" + (($promptPlannerInventory.Issues | ForEach-Object { "- $_" }) -join "`n")
    } else {
        ""
    }

    if ($VerifyOnly -or $promptPlannerInventory.Issues.Count -gt 0) {
        throw @"
The required Qwen prompt planner is incomplete or corrupt. $inventorySummary$issueDetails

Preserve unexpected files for inspection, then remove or replace them and rerun
Setup-H3-Studio.cmd. A clean partial download is resumed from the fixed revision.
"@
    }
    $promptPlannerMissingBytes = [int64]$promptPlannerInventory.MissingBytes
}

if ($env:OS -ne "Windows_NT") {
    throw "This pinned NIKU H STUDIO setup currently supports 64-bit Windows only."
}

if ($VerifyOnly) {
    Test-FixedCheckout -Path $ComfySource -ExpectedSha $ComfySha -Name "ComfyUI"
    Test-ComfyRuntime
    Test-ComfyModels -Lock $modelLock -FullHash:(-not $SkipModelHash)
    Test-PromptPlannerFiles -Lock $promptPlannerLock -FullHash:(-not $SkipModelHash)
    Test-PromptPlannerProvenance -Lock $promptPlannerLock
    Test-PromptPlannerRuntime
    Write-Host "ComfyUI verification completed without modifying the environment."
    return
}

if ($missingBytes -gt 0 -and -not $AcceptMiniMaxH3License) {
    $licenseUrl = [string]$modelLock.source.license_url
    throw @"
MiniMax H3 model files are not included in this repository.
Before downloading $missingBytes bytes, review the MiniMax H3 Community License:
$licenseUrl

The license has territory, use, redistribution, output, and commercial restrictions.
If you are eligible and accept it, rerun with -AcceptMiniMaxH3License or use Setup-H3-Studio.cmd.
"@
}

if ($missingBytes -gt 0 -or $promptPlannerMissingBytes -gt 0) {
    $driveName = [IO.Path]::GetPathRoot($Root).TrimEnd('\').TrimEnd(':')
    $drive = Get-PSDrive -Name $driveName
    $requiredFree = $missingBytes + $promptPlannerMissingBytes
    if ($missingBytes -gt 0) {
        $requiredFree += 25GB
    }
    if ($promptPlannerMissingBytes -gt 0) {
        $requiredFree += 10GB
    }
    if ([int64]$drive.Free -lt $requiredFree) {
        throw "Not enough free disk space for the runtime, models, and safety margins: need $requiredFree bytes, available $($drive.Free)."
    }
}

if ($missingBytes -gt 0) {
    Write-Host "MiniMax H3 license accepted for this setup invocation."
    Write-Host "Model download: $missingBytes bytes from fixed revision $($modelLock.source.revision)."
}

if ($promptPlannerMissingBytes -gt 0) {
    Write-Host "Required Apache-2.0 Qwen prompt planner download: $promptPlannerMissingBytes bytes from fixed revision $($promptPlannerLock.source.revision)."
    Write-Host "No additional click-through license acknowledgement is required; see MODEL_TERMS.md for attribution and the controlling license."
}

Assert-H3NvidiaPreflight

$PythonExe = Resolve-H3Python312 -Requested $PythonExe
Write-Host "Using Python 3.12: $PythonExe"

Set-FixedCheckout `
    -Path $ComfySource `
    -Repository "https://github.com/Comfy-Org/ComfyUI.git" `
    -ExpectedSha $ComfySha `
    -Name "ComfyUI"

if (-not (Test-Path -LiteralPath $ComfyPython -PathType Leaf)) {
    & $PythonExe -m venv $ComfyVenv
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the isolated ComfyUI environment: $ComfyVenv"
    }
}
if (-not (Test-H3Python312 -Path $ComfyPython)) {
    throw "The existing .comfy-venv is not a 64-bit Python 3.12 environment. Rename or remove '$ComfyVenv', then rerun setup."
}

& $ComfyPython -m pip install --upgrade pip==26.2
if ($LASTEXITCODE -ne 0) {
    throw "ComfyUI pip bootstrap failed."
}
& $ComfyPython -m pip install --no-deps --index-url https://download.pytorch.org/whl/cu130 `
    torch==2.13.0+cu130 torchvision==0.28.0+cu130 torchaudio==2.11.0+cu130
if ($LASTEXITCODE -ne 0) {
    throw "CUDA PyTorch installation failed."
}
& $ComfyPython -m pip install --requirement $Requirements
if ($LASTEXITCODE -ne 0) {
    throw "ComfyUI dependency installation failed."
}

if ($AttentionBackend -eq "sage") {
    & $ComfyPython -m pip install triton-windows==3.7.1.post27
    if ($LASTEXITCODE -ne 0) {
        throw "Pinned Triton Windows installation failed."
    }

    $SageWheelDirectory = Join-Path $Root ".cache\wheels"
    $SageWheelPath = Join-Path $SageWheelDirectory $SageWheelName
    New-Item -ItemType Directory -Path $SageWheelDirectory -Force | Out-Null
    $downloadSageWheel = -not (Test-Path -LiteralPath $SageWheelPath -PathType Leaf)
    if (-not $downloadSageWheel) {
        $sageWheelItem = Get-Item -LiteralPath $SageWheelPath
        $sageWheelHash = (Get-FileHash -LiteralPath $SageWheelPath -Algorithm SHA256).Hash.ToLowerInvariant()
        $downloadSageWheel = $sageWheelItem.Length -ne $SageWheelBytes -or $sageWheelHash -ne $SageWheelSha256
    }
    if ($downloadSageWheel) {
        $SagePartialPath = "$SageWheelPath.partial"
        Remove-Item -LiteralPath $SagePartialPath -Force -ErrorAction SilentlyContinue
        Invoke-WebRequest -Uri $SageWheelUrl -OutFile $SagePartialPath -UseBasicParsing
        $downloadedSageWheel = Get-Item -LiteralPath $SagePartialPath
        $downloadedSageHash = (Get-FileHash -LiteralPath $SagePartialPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($downloadedSageWheel.Length -ne $SageWheelBytes -or $downloadedSageHash -ne $SageWheelSha256) {
            throw "Pinned SageAttention wheel failed size or SHA-256 verification."
        }
        Remove-Item -LiteralPath $SageWheelPath -Force -ErrorAction SilentlyContinue
        Move-Item -LiteralPath $SagePartialPath -Destination $SageWheelPath -Force
    }
    $installedSage = @(& $ComfyPython -c "import importlib.metadata; print(importlib.metadata.version('sageattention'))" 2>$null)
    if ($LASTEXITCODE -ne 0 -or $installedSage.Count -eq 0 -or $installedSage[-1].Trim() -ne $SageVersion) {
        & $ComfyPython -m pip install --no-deps --force-reinstall $SageWheelPath
        if ($LASTEXITCODE -ne 0) {
            throw "Pinned SageAttention installation failed."
        }
    } else {
        Write-Host "Pinned SageAttention is already installed; reinstall skipped."
    }
} else {
    Write-Host "PyTorch attention selected; SageAttention wheel installation skipped."
}

if ($missingBytes -gt 0) {
    $modelRoot = Join-Path $Root "models\comfy"
    New-Item -ItemType Directory -Path $modelRoot -Force | Out-Null
    $patterns = @($modelLock.files | ForEach-Object {
        ([string]$_.path).Substring("models/comfy/".Length).Replace('\', '/')
    })
    $repoId = [string]$modelLock.source.repo_id
    $revision = [string]$modelLock.source.revision
    & $ComfyPython -c "import sys; from huggingface_hub import snapshot_download; snapshot_download(repo_id=sys.argv[1], revision=sys.argv[2], local_dir=sys.argv[3], allow_patterns=sys.argv[4:], max_workers=2)" `
        $repoId $revision $modelRoot @patterns
    if ($LASTEXITCODE -ne 0) {
        throw "Fixed-revision Comfy model download failed. It is safe to rerun this script to resume."
    }
} else {
    Write-Host "All fixed-revision Comfy model files are already present; download skipped."
}

if ($promptPlannerMissingBytes -gt 0) {
    New-Item -ItemType Directory -Path $PromptPlannerRoot -Force | Out-Null
    $plannerPrefix = "models/prompt_planner/Qwen3-4B-Instruct-2507/"
    $plannerPatterns = @($promptPlannerLock.files | ForEach-Object {
        $normalizedPath = ([string]$_.path).Replace('\', '/')
        if (-not $normalizedPath.StartsWith($plannerPrefix, [System.StringComparison]::Ordinal)) {
            throw "Prompt planner lock path is outside the expected model root: $normalizedPath"
        }
        $normalizedPath.Substring($plannerPrefix.Length)
    })
    $plannerRepoId = [string]$promptPlannerLock.source.repo_id
    $plannerRevision = [string]$promptPlannerLock.source.revision
    & $ComfyPython -c "import sys; from huggingface_hub import snapshot_download; snapshot_download(repo_id=sys.argv[1], revision=sys.argv[2], local_dir=sys.argv[3], allow_patterns=sys.argv[4:], max_workers=2)" `
        $plannerRepoId $plannerRevision $PromptPlannerRoot @plannerPatterns
    if ($LASTEXITCODE -ne 0) {
        throw "Fixed-revision Qwen prompt planner download failed. It is safe to rerun this script to resume."
    }
} else {
    Write-Host "All fixed-revision Qwen prompt planner files are already present; download skipped."
}

Test-ComfyRuntime
Test-ComfyModels -Lock $modelLock -FullHash:(-not $SkipModelHash)
Test-PromptPlannerFiles -Lock $promptPlannerLock -FullHash
Write-PromptPlannerProvenance -Lock $promptPlannerLock
Test-PromptPlannerProvenance -Lock $promptPlannerLock
Test-PromptPlannerRuntime -LoadWeights:(-not $VerifyOnly)

Write-Host "ComfyUI setup complete."
Write-Host "ComfyUI SHA: $ComfySha"
Write-Host "Model revision: $($modelLock.source.revision)"
Write-Host "Prompt planner revision: $($promptPlannerLock.source.revision)"
Write-Host "Attention backend: $AttentionBackend$(if ($AttentionBackend -eq 'sage') { " ($SageVersion; verified wheel)" } else { '' })"
