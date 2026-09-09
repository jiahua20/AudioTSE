# 构建 @audiotse/gate-napi 的 N-API addon：
#   激活 VS 环境 → 安装 devDependencies → 检查 sherpa 预编译包 → cmake-js 编译。
# 产物：build/<Config>/audiotse_gate_napi.node + 同目录 4 个运行时 dll。
# 用法：.\build.ps1                Release 构建
#       .\build.ps1 -Config Debug
param(
    [ValidateSet('Release', 'Debug')]
    [string]$Config = 'Release'
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$cppRoot = Join-Path $root '..\cpp'

# 1) 激活 VS 开发环境（cmake/ninja/cl 进当前会话，幂等）
. (Join-Path $cppRoot 'Activate-VS.ps1')

# 2) devDependencies（cmake-js / node-addon-api），首次运行安装
if (-not (Test-Path (Join-Path $root 'node_modules'))) {
    Write-Host '安装 devDependencies...' -ForegroundColor Cyan
    npm install
    if ($LASTEXITCODE -ne 0) { throw 'npm install 失败' }
}
# include_dir 返回相对路径（相对 cwd），统一转绝对路径，避免受构建源根位置影响
$addonApi = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath((& node -p "require('node-addon-api').include_dir").Trim())
if (-not $addonApi -or -not (Test-Path $addonApi)) {
    throw "无法定位 node-addon-api 头文件目录（先 npm install）"
}

# 3) sherpa-onnx 预编译包（与 sdk/cpp 共用 third_party/）
$thirdParty = Join-Path $cppRoot 'third_party'
$sherpa = Get-ChildItem $thirdParty -Directory -Filter 'sherpa-onnx-*' -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending | Select-Object -First 1
if (-not $sherpa) {
    throw "缺少 sherpa-onnx 预编译包：先在 sdk\cpp 运行 .\build.ps1（会自动下载到 third_party\）"
}
Write-Host "使用 sherpa-onnx 预编译包: $($sherpa.FullName)" -ForegroundColor DarkGray

# 4) cmake-js 编译（node 头文件由 cmake-js 自动下载提供）。
#    注意 1：直接用 node 调 cmake-js 而不经 npx——npx 会把 node_modules\.bin 塞进
#    PATH，其中 npm 的 rc 包会被 CMake 误认成 Windows 资源编译器 rc.exe 导致链接失败。
#    注意 2：cmake-js 的 CLI：-G 才是生成器（-g 是偏好 GNU 编译器）、-B 是构建配置、
#    给 CMake 传自定义参数必须用 --CD键=值 长格式（-D 是它的 debug 开关）。
$cmakeJs = Join-Path $root 'node_modules\cmake-js\bin\cmake-js'
& node $cmakeJs compile -G Ninja -B $Config "--CDNODE_ADDON_API_DIR=$addonApi" "--CDAUDIOTSE_SHERPA_ROOT=$($sherpa.FullName)"
if ($LASTEXITCODE -ne 0) { throw 'cmake-js 编译失败' }

$node = Join-Path $root 'build' $Config 'audiotse_gate_napi.node'
if (-not (Test-Path $node)) { $node = Join-Path $root 'build' 'audiotse_gate_napi.node' }
Write-Host "`n构建完成: $node" -ForegroundColor Green
Write-Host '运行所需 dll 已拷贝到同目录（Electron/Node 直接 require 本包即可）' -ForegroundColor DarkGray
