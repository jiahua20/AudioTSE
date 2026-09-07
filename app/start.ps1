# 一键启动脚本：环境检查 → 依赖/模型准备 → 启动 Python 后端 + Electron 桌面端。
# 用法：.\start.ps1           启动应用
#       .\start.ps1 -SetupOnly  只做安装检查，不启动
param(
    [switch]$SetupOnly
)

$ErrorActionPreference = 'Stop'   # 任何命令出错立即终止脚本
$root = $PSScriptRoot             # 项目根目录（脚本所在处）
$backendProcess = $null           # 后端进程句柄（结束时用于清理）

function Require-Command([string]$Name) {
    # 确保外部命令（conda/npm）可用，缺失则报错提示安装
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Missing command: $Name"
    }
}

function Get-AudioTsePython {
    # 从 conda 的 JSON 输出里找名为 AudioTSE 的环境，返回其 python.exe 完整路径
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

Set-Location $root          # 统一在项目根目录下执行
Require-Command 'conda'     # 前置：Miniconda/Anaconda 已装
Require-Command 'npm'       # 前置：Node.js 18.20+ 已装
$python = Get-AudioTsePython

# [1/3] Python 依赖：试探性 import，失败才走 pip install（省每次启动的安装检查）
Write-Host '[1/3] Checking Python dependencies...'
& $python -c 'import numpy, sherpa_onnx, websockets' 2>$null
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install -r (Join-Path $root 'backend\requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
}

# [2/3] 运行时模型：已下载的秒过，缺的现场下载（ASR/VAD/声纹）
Write-Host '[2/3] Checking runtime models...'
& $python (Join-Path $root 'scripts\download-models.py')
if ($LASTEXITCODE -ne 0) { throw 'Model setup failed.' }

# [3/3] 桌面端依赖：node_modules 里缺 vue/electron 才执行 npm install
Write-Host '[3/3] Checking desktop dependencies...'
if (-not (Test-Path (Join-Path $root 'node_modules\vue\package.json')) -or
    -not (Test-Path (Join-Path $root 'node_modules\electron\package.json'))) {
    & npm.cmd install
    if ($LASTEXITCODE -ne 0) { throw 'Desktop dependency installation failed.' }
}

# -SetupOnly：到此为止，只保证环境就绪，不启动应用
if ($SetupOnly) {
    Write-Host 'AudioTSE setup is ready.' -ForegroundColor Green
    return
}

# 后端占用检查：8765 端口已有监听（比如上次没退干净）则复用，不重复启动
$backendAlreadyRunning = [bool](Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue)
if ($backendAlreadyRunning) {
    Write-Host 'Backend is already listening on ws://127.0.0.1:8765'
} else {
    # PYTHONPATH 指向 backend/，使 `audio_tse` 包可被 import
    $env:PYTHONPATH = Join-Path $root 'backend'
    # 启动后端（python -m audio_tse.server），同窗口输出日志
    $backendProcess = Start-Process -FilePath $python -ArgumentList '-m', 'audio_tse.server' -WorkingDirectory $root -NoNewWindow -PassThru
    Write-Host "Backend started (PID $($backendProcess.Id))."
}

# 桌面端 = vite dev server(5173) + electron 双进程并发（见 package.json 的 desktop 脚本）
# electron 内部会等 5173 就绪再开窗口；Ctrl+C 或关闭窗口即退出
Write-Host 'Starting AudioTSE desktop... Press Ctrl+C to stop.' -ForegroundColor Green
try {
    & npm.cmd run desktop
}
finally {
    # 桌面端退出后连带停掉后端进程，避免遗留占端口的孤儿进程
    if ($backendProcess -and -not $backendProcess.HasExited) {
        Stop-Process -Id $backendProcess.Id -Force
        Write-Host 'Backend stopped.'
    }
}
