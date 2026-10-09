param(
    [string]$EnvironmentPrefix = 'D:\Dev\conda-envs\creative-poster',
    [string]$CondaExecutable = 'D:\Dev\miniconda3\Scripts\conda.exe',
    [string]$UvCache = 'D:\Dev\uv-cache'
)
$ErrorActionPreference = 'Stop'
$taskProjectRoot = Split-Path $PSScriptRoot -Parent
Push-Location $taskProjectRoot
try {
    if (-not (Test-Path -LiteralPath (Join-Path $EnvironmentPrefix 'python.exe'))) {
        & $CondaExecutable create --prefix $EnvironmentPrefix python=3.11 pip -y
        if ($LASTEXITCODE -ne 0) { throw 'Conda environment creation failed' }
    }
    $taskPython = Join-Path $EnvironmentPrefix 'python.exe'
    $env:UV_CACHE_DIR = $UvCache
    $env:UV_PYTHON_DOWNLOADS = 'never'
    New-Item -ItemType Directory -Path '.scratch' -Force | Out-Null
    uv export --python $taskPython --locked --all-groups --no-emit-project --output-file .scratch/locked-dependencies.txt | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Locked dependency export failed' }
    uv pip install --python $taskPython -r .scratch/locked-dependencies.txt --editable .
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
    & $taskPython scripts/fetch-visual-fonts.py
    if ($LASTEXITCODE -ne 0) { throw 'Font download/verification failed' }
    & $taskPython -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
} finally { Pop-Location }
