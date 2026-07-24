param(
    [switch]$SetupOnly
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$backendProcess = $null

function Require-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Missing command: $Name"
    }
}

function Get-AudioTsePython {
    $condaInfo = (& conda env list --json | ConvertFrom-Json)
    $environment = $condaInfo.envs | Where-Object { (Split-Path $_ -Leaf) -eq 'AudioTSE' } | Select-Object -First 1
    if (-not $environment) {
        throw 'Conda environment AudioTSE was not found.'
    }

    $python = Join-Path $environment 'python.exe'
    if (-not (Test-Path $python)) {
        throw "Python executable was not found: $python"
    }
    return $python
}

Set-Location $root
Require-Command 'conda'
Require-Command 'npm'
$python = Get-AudioTsePython

Write-Host '[1/3] Checking Python dependencies...'
& $python -c 'import numpy, sherpa_onnx, websockets' 2>$null
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install -r (Join-Path $root 'backend\requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
}

Write-Host '[2/3] Checking runtime models...'
& $python (Join-Path $root 'scripts\download-models.py')
if ($LASTEXITCODE -ne 0) { throw 'Model setup failed.' }

Write-Host '[3/3] Checking desktop dependencies...'
if (-not (Test-Path (Join-Path $root 'node_modules\vue\package.json')) -or
    -not (Test-Path (Join-Path $root 'node_modules\electron\package.json'))) {
    & npm.cmd install
    if ($LASTEXITCODE -ne 0) { throw 'Desktop dependency installation failed.' }
}

if ($SetupOnly) {
    Write-Host 'AudioTSE setup is ready.' -ForegroundColor Green
    return
}

$backendAlreadyRunning = [bool](Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue)
if ($backendAlreadyRunning) {
    Write-Host 'Backend is already listening on ws://127.0.0.1:8765'
} else {
    $env:PYTHONPATH = Join-Path $root 'backend'
    $backendProcess = Start-Process -FilePath $python -ArgumentList '-m', 'audio_tse.server' -WorkingDirectory $root -NoNewWindow -PassThru
    Write-Host "Backend started (PID $($backendProcess.Id))."
}

Write-Host 'Starting AudioTSE desktop... Press Ctrl+C to stop.' -ForegroundColor Green
try {
    & npm.cmd run desktop
} finally {
    if ($backendProcess -and -not $backendProcess.HasExited) {
        Stop-Process -Id $backendProcess.Id -Force
        Write-Host 'Backend stopped.'
    }
}