# 内网离线交付打包脚本（napi 版）：把 C++ N-API SDK（core 入口）运行所需的一切打成 zip。
# 产出：sdk\audiotse-intranet-napi-<时间戳>.zip（与 web 版 sdk\audiotse-intranet-web-*.zip
#       并列，两包同 API 同基准，供内网对照使用；可用 -OutputDir 改输出位置）。
# 内容：SDK 包装（index.js + 类型）+ native/（.node + sherpa 1.12.1 dll + VC++ 运行库）
#       + 声纹模型 + 样例音频 + 离线自检。
#
# 用法（在任意目录）：
#   powershell -File sdk\cpp-napi\script\package-intranet-napi.ps1
#
# 前置：sdk\cpp-napi 下已 .\build.ps1（build\Release\audiotse_gate_napi.node 就位）。
param(
    [string]$OutputDir = ""
)

$ErrorActionPreference = 'Stop'

$root = Split-Path $PSScriptRoot -Parent                  # sdk\cpp-napi
$repoRoot = Split-Path (Split-Path $root -Parent) -Parent # 仓库根
$addon = Join-Path $root 'build\Release\audiotse_gate_napi.node'
$speakerModel = Join-Path $repoRoot 'app\models\sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k\model.onnx'
$samplesDir = Join-Path $repoRoot 'app\samples'

# ── 前置检查 ────────────────────────────────────────────────
if (-not (Test-Path $addon)) { throw "缺 $addon —— 请先在 sdk\cpp-napi 运行 .\build.ps1" }
foreach ($f in @($speakerModel, (Join-Path $samplesDir 'enroll_target.wav'), (Join-Path $samplesDir 'target_clean.wav'), (Join-Path $samplesDir 'other_clean.wav'))) {
    if (-not (Test-Path $f)) { throw "缺文件：$f" }
}

# ── 准备暂存目录 ────────────────────────────────────────────
# 默认产出到 sdk\ 根目录（zip + 同名解压目录），所有交付物集中可见；
# 需要另放时用 -OutputDir 覆盖
if (-not $OutputDir) { $OutputDir = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent }  # sdk\
$stamp = Get-Date -Format 'yyyyMMdd-HHmm'
$stage = Join-Path $OutputDir "audiotse-intranet-napi-$stamp"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path (Join-Path $stage 'gate-napi\examples'), (Join-Path $stage 'gate-napi\native'), (Join-Path $stage 'models'), (Join-Path $stage 'samples') | Out-Null

# ── 1. SDK 包装层（index.js 会自动探测 native/ 下的 .node）──
Copy-Item (Join-Path $root 'index.js'), (Join-Path $root 'index.d.ts') (Join-Path $stage 'gate-napi\')
Copy-Item (Join-Path $root 'examples\*.js') (Join-Path $stage 'gate-napi\examples\')
$gatePackageJson = @'
{
  "name": "audiotse-gate-napi",
  "version": "0.3.0",
  "description": "AudioTSE voiceprint gate SDK (C++ N-API addon, core entry, intranet delivery)",
  "main": "index.js",
  "types": "index.d.ts"
}
'@
[IO.File]::WriteAllText((Join-Path $stage 'gate-napi\package.json'), $gatePackageJson)

# ── 2. native/：addon + sherpa 运行时 dll（build.ps1 已拷到 .node 同目录）──
$buildDir = Split-Path $addon -Parent
Copy-Item (Join-Path $buildDir '*.node'), (Join-Path $buildDir '*.dll') (Join-Path $stage 'gate-napi\native\')

# ── 2.5 VC++ 运行库（app-local 免安装，放 .node 同目录）────
# 内网机器常没装 VC++ 2015-2022 运行库，缺它加载 addon 依赖链会报 126。Node 的
# dlopen 用 LOAD_WITH_ALTERED_SEARCH_PATH，优先从被加载模块所在目录解析依赖。
$vcRuntimes = @('msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_atomic_wait.dll', 'vcruntime140.dll', 'vcruntime140_1.dll')
$vcTarget = Join-Path $stage 'gate-napi\native'
foreach ($dll in $vcRuntimes) {
    $src = Join-Path $env:SystemRoot "System32\$dll"   # x64 系统 System32 即 x64 版本
    if (-not (Test-Path $src)) { throw "打包机缺 $src —— 请先装 VC++ 2015-2022 运行库(x64, vc_redist.x64.exe)" }
    Copy-Item $src (Join-Path $vcTarget $dll)
}
Write-Host ("已内置 VC++ 运行库(app-local)：{0}（版本 {1}）" -f ($vcRuntimes -join ', '), (Get-Item (Join-Path $vcTarget 'vcruntime140.dll')).VersionInfo.FileVersion)

# ── 3. 模型与样例音频 ─────────────────────────────────────
Copy-Item $speakerModel (Join-Path $stage 'models\speaker.onnx')
Copy-Item (Join-Path $samplesDir 'enroll_target.wav'), (Join-Path $samplesDir 'target_clean.wav'), (Join-Path $samplesDir 'other_clean.wav') (Join-Path $stage 'samples\')

# ── 4. 交付说明与离线自检 ─────────────────────────────────
Copy-Item (Join-Path $PSScriptRoot 'intranet-README.md') (Join-Path $stage 'README.md')
Copy-Item (Join-Path $PSScriptRoot 'env-check.js') (Join-Path $stage 'env-check.js')
Copy-Item (Join-Path $PSScriptRoot 'smoke-test.js') (Join-Path $stage 'smoke-test.js')

# ── 5. 压缩 ───────────────────────────────────────────────
$zip = "$stage.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -Force

$stageMb = [math]::Round((Get-ChildItem $stage -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
$zipMb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host "打包完成：$zip"
Write-Host "  解压后 ${stageMb} MB / zip ${zipMb} MB（C++ addon + sherpa 1.12.1 运行时 + VC++ 运行库）"
Write-Host "拿走前建议本机先自检：解压 zip 后在解压目录运行  node env-check.js && node smoke-test.js"
