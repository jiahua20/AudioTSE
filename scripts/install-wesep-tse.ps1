# 实验性纯音频 TSE 的安装脚本（约 300+ MB 下载，只装一次）：
#   [1/3] CPU 版 PyTorch（不带 CUDA，体积小一半以上）
#   [2/3] wesep + wespeaker（--no-deps 装 wespeaker，避开重依赖；
#         缺的 wesep.utils 由 backend 的 wesep_loader.py 垫片在运行时补上）
#   [3/3] WeSep BSRNN 权重（约 262 MB，从 ModelScope 下载）
# 装完后重跑 start.ps1，界面「纯音频 TSE（实验）」即变为可选。
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent    # 项目根目录（scripts/ 的上一级）
# 定位 AudioTSE conda 环境里的 python.exe
$condaInfo = (& conda env list --json | ConvertFrom-Json)
$environment = $condaInfo.envs | Where-Object { (Split-Path $_ -Leaf) -eq 'AudioTSE' } | Select-Object -First 1
if (-not $environment) {
    throw 'Conda environment AudioTSE was not found.'
}

$python = Join-Path $environment 'python.exe'
# CPU 专用 PyTorch 源（比默认的 CUDA 版小很多）
Write-Host '[1/3] Installing CPU PyTorch...'
& $python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
if ($LASTEXITCODE -ne 0) { throw 'CPU PyTorch installation failed.' }

# wesep 从 GitHub master 装；wespeaker 用 --no-deps 装（其重依赖本项目用不到，
# 运行时的导入问题由 backend/audio_tse/wesep_loader.py 的垫片解决）
Write-Host '[2/3] Installing WeSep runtime...'
& $python -m pip install PyYAML requests soundfile silero-vad 'git+https://github.com/wenet-e2e/wesep.git'
if ($LASTEXITCODE -ne 0) { throw 'WeSep installation failed.' }
& $python -m pip install --no-deps 'https://github.com/wenet-e2e/wespeaker/archive/refs/heads/master.zip'
if ($LASTEXITCODE -ne 0) { throw 'WeSpeaker model definitions installation failed.' }

# 下载并部署 BSRNN+ECAPA 权重到 models/wesep-bsrnn-ecapa-vox1/
Write-Host '[3/3] Downloading WeSep BSRNN model (about 262 MB)...'
& $python (Join-Path $root 'scripts\prepare-wesep-tse.py')
if ($LASTEXITCODE -ne 0) { throw 'WeSep model setup failed.' }

Write-Host 'Experimental pure-audio TSE is ready. Restart AudioTSE to enable it.' -ForegroundColor Green
