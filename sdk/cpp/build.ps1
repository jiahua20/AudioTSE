# 一键构建：激活 VS 环境 → 准备 sherpa-onnx 预编译包（缺失则下载）→ cmake configure + build。
# 用法：.\build.ps1                Release 构建
#       .\build.ps1 -Config Debug
param(
    [ValidateSet('Release', 'Debug', 'RelWithDebInfo', 'MinSizeRel')]
    [string]$Config = 'Release'
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

# 1) 激活 VS 开发环境（cmake/ninja/cl 进当前会话，幂等）
. (Join-Path $root 'Activate-VS.ps1')

# 2) sherpa-onnx 预编译包（官方 win-x64 shared MD Release，含 include/ lib/ bin/）。
#    目录保留官方发布全名（含版本号），便于确认在用的版本。
$thirdParty = Join-Path $root 'third_party'
$version = '1.13.7'
$asset = "sherpa-onnx-v$version-win-x64-shared-MD-Release"
$sherpaRoot = Join-Path $thirdParty $asset
if (-not (Test-Path (Join-Path $sherpaRoot 'include'))) {
    $url = "https://github.com/k2-fsa/sherpa-onnx/releases/download/v$version/$asset.tar.bz2"
    New-Item -ItemType Directory -Force $thirdParty | Out-Null
    $tb = Join-Path $thirdParty "$asset.tar.bz2"
    Write-Host "下载 $url" -ForegroundColor Cyan
    curl.exe -sSL -o $tb $url
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $tb)) { throw "下载失败" }
    Write-Host "解压到 $sherpaRoot" -ForegroundColor Cyan
    $py = 'D:\App\miniconda3\envs\AudioTSE\python.exe'   # tar.exe 解压 bz2 不稳，用 Python
    & $py -c "import tarfile; t = tarfile.open(r'$tb', 'r:bz2'); t.extractall(r'$thirdParty'); t.close()"
    if ($LASTEXITCODE -ne 0) { throw "解压失败" }
    Remove-Item $tb
    if (-not (Test-Path (Join-Path $sherpaRoot 'include'))) { throw "解压结果缺少 include/: $sherpaRoot" }
}

# 3) cmake configure + build（Ninja 单配置生成器）。
#    -D 参数必须整体加引号：PowerShell 对裸的 -DX=$v 不做变量展开，会把字面量传给 cmake。
$build = Join-Path $root 'build'
cmake -S $root -B $build -G Ninja "-DCMAKE_BUILD_TYPE=$Config" "-DAUDIOTSE_SHERPA_ROOT=$sherpaRoot"
if ($LASTEXITCODE -ne 0) { throw "cmake configure 失败" }
cmake --build $build
if ($LASTEXITCODE -ne 0) { throw "cmake build 失败" }

Write-Host "`n构建完成: $build\gate_demo.exe" -ForegroundColor Green
Write-Host "运行所需 dll 已拷贝到 $build（直接运行即可）" -ForegroundColor DarkGray
