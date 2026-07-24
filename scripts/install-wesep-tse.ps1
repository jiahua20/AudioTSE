$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$condaInfo = (& conda env list --json | ConvertFrom-Json)
$environment = $condaInfo.envs | Where-Object { (Split-Path $_ -Leaf) -eq 'AudioTSE' } | Select-Object -First 1
if (-not $environment) {
    throw 'Conda environment AudioTSE was not found.'
}

$python = Join-Path $environment 'python.exe'
Write-Host '[1/3] Installing CPU PyTorch...'
& $python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
if ($LASTEXITCODE -ne 0) { throw 'CPU PyTorch installation failed.' }

Write-Host '[2/3] Installing WeSep runtime...'
& $python -m pip install PyYAML requests soundfile silero-vad 'git+https://github.com/wenet-e2e/wesep.git'
if ($LASTEXITCODE -ne 0) { throw 'WeSep installation failed.' }
& $python -m pip install --no-deps 'https://github.com/wenet-e2e/wespeaker/archive/refs/heads/master.zip'
if ($LASTEXITCODE -ne 0) { throw 'WeSpeaker model definitions installation failed.' }

Write-Host '[3/3] Downloading WeSep BSRNN model (about 262 MB)...'
& $python (Join-Path $root 'scripts\prepare-wesep-tse.py')
if ($LASTEXITCODE -ne 0) { throw 'WeSep model setup failed.' }

Write-Host 'Experimental pure-audio TSE is ready. Restart AudioTSE to enable it.' -ForegroundColor Green