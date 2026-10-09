param(
    [string]$EnvironmentPrefix='D:\Dev\conda-envs\creative-poster',
    [string]$ModelDirectory='D:\Models\sdxl-base-1.0',
    [string]$UvCache='D:\Dev\uv-cache',
    [switch]$SkipModelDownload
)
$ErrorActionPreference='Stop'
$taskPython=Join-Path $EnvironmentPrefix 'python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Run scripts/setup-visual.ps1 first' }
$taskRoot=Split-Path $PSScriptRoot -Parent
Push-Location $taskRoot
try {
    $env:UV_CACHE_DIR=$UvCache
    $env:UV_PYTHON_DOWNLOADS='never'
    New-Item -ItemType Directory -Path '.scratch' -Force | Out-Null
    uv export --python $taskPython --locked --extra local-gpu --all-groups --no-emit-project --emit-index-url --output-file .scratch/local-gpu-dependencies.txt | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Locked GPU dependency export failed' }
    uv pip install --python $taskPython --index-strategy unsafe-best-match -r .scratch/local-gpu-dependencies.txt --editable .
    if ($LASTEXITCODE -ne 0) { throw 'GPU dependency installation failed' }
    & $taskPython -c "import torch; assert torch.cuda.is_available(); print(torch.__version__,torch.cuda.get_device_name(0)); print((torch.ones(3,device='cuda')*2).tolist())"
    if ($LASTEXITCODE -ne 0) { throw 'CUDA smoke test failed' }
    if (-not $SkipModelDownload) {
        & $taskPython scripts/fetch-sdxl.py --model-dir $ModelDirectory
        if ($LASTEXITCODE -ne 0) { throw 'SDXL model verification failed' }
    }
    $env:POSTER_SDXL_MODEL_DIR=$ModelDirectory
    Write-Output "Set POSTER_SDXL_MODEL_DIR=$ModelDirectory in future shells before running the case CLI."
} finally { Pop-Location }
