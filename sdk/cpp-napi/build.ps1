# 构建 @audiotse/gate-napi 的 N-API addon（自包含构建根）：
#   激活 VS 环境 → 安装 devDependencies → 检查 sherpa 预编译包 → cmake-js 编译。
# 产物：build/<Config>/audiotse_gate_napi.node + 同目录 4 个运行时 dll。
# sherpa 版本须与内网 / sdk/web 一致（1.12.1），见 cmake/sherpa-onnx.cmake 注释。
# 用法：.\build.ps1                Release 构建
#       .\build.ps1 -Config Debug
param(
    [ValidateSet('Release', 'Debug')]
    [string]$Config = 'Release'
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

# 1) 激活 VS 开发环境（cmake/ninja/cl 进当前会话，幂等）
. (Join-Path $root 'Activate-VS.ps1')

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

# 3) sherpa-onnx 预编译包（third_party/，取字典序最大；应只有 1.12.1 一个）
$thirdParty = Join-Path $root 'third_party'
$sherpa = Get-ChildItem $thirdParty -Directory -Filter 'sherpa-onnx-*' -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending | Select-Object -First 1
if (-not $sherpa) {
    throw "缺少 sherpa-onnx 预编译包：下载 sherpa-onnx-v1.12.1-win-x64-shared.tar.bz2 解压到 third_party\（版本必须 1.12.1，与内网一致）"
}
Write-Host "使用 sherpa-onnx 预编译包: $($sherpa.FullName)" -ForegroundColor DarkGray

# 3.5) 1.12.1 包不带 onnxruntime.lib（导入库），缺则从 dll 生成（dumpbin /exports + lib /def）
$ortLib = Join-Path $sherpa.FullName 'lib\onnxruntime.lib'
if (-not (Test-Path $ortLib)) {
    Write-Host 'onnxruntime.lib 缺失，从 dll 生成导入库...' -ForegroundColor Cyan
    Push-Location (Join-Path $sherpa.FullName 'lib')
    try {
        $names = (dumpbin /exports onnxruntime.dll) |
            Where-Object { $_ -match '^\s+\d+\s+[0-9A-Fa-f]+\s+[0-9A-Fa-f]+\s+(\S+)' } |
            ForEach-Object { $Matches[1] }
        if (-not $names) { throw '未能解析 onnxruntime.dll 的导出符号' }
        $defContent = "LIBRARY onnxruntime`r`nEXPORTS`r`n" + (($names | ForEach-Object { $_ }) -join "`r`n")
        [IO.File]::WriteAllText((Join-Path (Get-Location) 'onnxruntime.def'), $defContent)
        lib /def:onnxruntime.def /machine:x64 /out:onnxruntime.lib | Out-Null
    } finally {
        Pop-Location
    }
    if (-not (Test-Path $ortLib)) { throw "onnxruntime.lib 生成失败：$ortLib" }
    Write-Host "已生成 $ortLib（导出：$($names -join ', ')）" -ForegroundColor DarkGray
}

# 4) cmake-js 编译（node 头文件由 cmake-js 自动下载提供）。
#    注意 1：直接用 node 调 cmake-js 而不经 npx——npx 会把 node_modules\.bin 塞进
#    PATH，其中 npm 的 rc 包会被 CMake 误认成 Windows 资源编译器 rc.exe 导致链接失败。
#    注意 2：cmake-js 的 CLI：-G 才是生成器（-g 是偏好 GNU 编译器）、-B 是构建配置、
#    给 CMake 传自定义参数必须用 --CD键=值 长格式（-D 是它的 debug 开关）。
$cmakeJs = Join-Path $root 'node_modules\cmake-js\bin\cmake-js'
& node $cmakeJs compile -G Ninja -B $Config "--CDNODE_ADDON_API_DIR=$addonApi" "--CDAUDIOTSE_SHERPA_ROOT=$($sherpa.FullName)"
if ($LASTEXITCODE -ne 0) { throw 'cmake-js 编译失败' }

$node = Join-Path $root "build\$Config\audiotse_gate_napi.node"
if (-not (Test-Path $node)) { $node = Join-Path $root 'build\audiotse_gate_napi.node' }
Write-Host "`n构建完成: $node" -ForegroundColor Green
Write-Host '运行所需 dll 已拷贝到同目录（Electron/Node 直接 require 本包即可）' -ForegroundColor DarkGray
