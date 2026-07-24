$ErrorActionPreference = 'Stop'
$condaInfo = (& conda env list --json | ConvertFrom-Json)
$environment = $condaInfo.envs | Where-Object { (Split-Path $_ -Leaf) -eq 'AudioTSE' } | Select-Object -First 1
if (-not $environment) { throw 'Conda environment AudioTSE was not found' }
$python = Join-Path $environment 'python.exe'
& $python (Join-Path $PSScriptRoot 'download-models.py') zipformer
if ($LASTEXITCODE -ne 0) { throw 'ASR model download failed' }