# 内网离线交付打包脚本（web 版）：把声纹过滤 SDK（core 入口）运行所需的一切打成一个 zip。
# 产出：sdk\audiotse-intranet-web-<时间戳>.zip（与 napi 版 sdk\audiotse-intranet-napi-*.zip
#       并列，两包同 API 同基准，供内网对照使用；可用 -OutputDir 改输出位置）。
# 内容：SDK 产物（含 UMD 单文件）+ sherpa-onnx 原生运行时闭包 + VC++ 运行库
#       + 声纹模型 + 样例音频 + 离线自检脚本。
#
# 依赖说明：SDK 推理走 sherpa-onnx-node 1.12.1（与内网项目同版本）。宿主项目若已
# 自带 sherpa-onnx-node，SDK 与其共用同一 onnxruntime 运行时，进程内不会出现第二份
# onnxruntime.dll（旧版依赖 onnxruntime-node 时两份同名 dll 在 Windows 下按模块名
# 去重会错配，报「无法运行 %1」193）。闭包仅 sherpa-onnx-node + sherpa-onnx-win-x64
# 两个包（均无传递依赖），比旧版 onnxruntime-node 闭包（~284MB）小一个数量级。
#
# 用法（在任意目录）：
#   powershell -File sdk\web\script\package-intranet.ps1
#
# 前置：sdk\web 下已 npm install 且已 npm run build（脚本会检查）。
param(
    [string]$OutputDir = ""
)

$ErrorActionPreference = 'Stop'

$webDir = Split-Path $PSScriptRoot -Parent                  # sdk\web
$repoRoot = Split-Path (Split-Path $webDir -Parent) -Parent # 仓库根
$speakerModel = Join-Path $repoRoot 'app\models\sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k\model.onnx'
$samplesDir = Join-Path $repoRoot 'app\samples'

# ── 前置检查 ────────────────────────────────────────────────
$distCore = Join-Path $webDir 'dist\core\index.js'
if (-not (Test-Path $distCore)) { throw "缺 $distCore —— 请先在 sdk\web 下运行 npm run build" }
$sherpaNode = Join-Path $webDir 'node_modules\sherpa-onnx-node'
$sherpaBinDir = Join-Path $webDir 'node_modules\sherpa-onnx-win-x64'
if (-not (Test-Path (Join-Path $sherpaBinDir 'sherpa-onnx.node'))) { throw "缺 $sherpaBinDir —— 请先在 sdk\web 下运行 npm install" }
foreach ($f in @($speakerModel, (Join-Path $samplesDir 'enroll_target.wav'), (Join-Path $samplesDir 'target_clean.wav'), (Join-Path $samplesDir 'other_clean.wav'))) {
    if (-not (Test-Path $f)) { throw "缺文件：$f" }
}

# ── 0. UMD 单文件（内网宿主免编译直引；源码见 script/build-umd.js）──
& node (Join-Path $PSScriptRoot 'build-umd.js')
if ($LASTEXITCODE -ne 0) { throw "UMD 构建失败" }

# ── 准备暂存目录 ────────────────────────────────────────────
# 默认产出到 sdk\ 根目录（zip + 同名解压目录），所有交付物集中可见；
# 需要另放时用 -OutputDir 覆盖
if (-not $OutputDir) { $OutputDir = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent }  # sdk\
$stamp = Get-Date -Format 'yyyyMMdd-HHmm'
$stage = Join-Path $OutputDir "audiotse-intranet-web-$stamp"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path (Join-Path $stage 'gate\dist'), (Join-Path $stage 'gate\examples'), (Join-Path $stage 'models'), (Join-Path $stage 'samples'), (Join-Path $stage 'node_modules') | Out-Null

# ── 1. SDK 产物（core 入口 + UMD 单文件）────────────────────
Copy-Item -Recurse (Join-Path $webDir 'dist\core') (Join-Path $stage 'gate\dist\')
Copy-Item (Join-Path $webDir 'examples\*.js') (Join-Path $stage 'gate\examples\')
Copy-Item (Join-Path $webDir 'dist\umd\audiotse-gate.umd.js'), (Join-Path $webDir 'dist\umd\audiotse-gate.d.ts') (Join-Path $stage 'gate\')
# 包级 package.json：require('<本包>/gate') 命中 UMD 单文件（多文件版仍可用 dist/core）
$gatePackageJson = @'
{
  "name": "audiotse-gate",
  "version": "0.3.0",
  "description": "AudioTSE voiceprint gate SDK (core entry, no VAD, sherpa-onnx-node runtime)",
  "main": "audiotse-gate.umd.js",
  "types": "audiotse-gate.d.ts"
}
'@
[IO.File]::WriteAllText((Join-Path $stage 'gate\package.json'), $gatePackageJson)

# ── 2. 原生运行时闭包（两个包，均无传递依赖）────────────────
Copy-Item -Recurse $sherpaNode (Join-Path $stage 'node_modules\sherpa-onnx-node')
Copy-Item -Recurse $sherpaBinDir (Join-Path $stage 'node_modules\sherpa-onnx-win-x64')

# ── 2.5 VC++ 运行库（app-local 免安装）──────────────────
# 内网机器常没装 VC++ 2015-2022 运行库，缺它加载 sherpa-onnx.node 的依赖链会报
# 「The specified module could not be found」(错误 126)。DLL 放在 sherpa-onnx.node
# 同目录即可：Node 的 dlopen 用 LOAD_WITH_ALTERED_SEARCH_PATH，优先从被加载
# 模块所在目录解析依赖（微软许可允许随应用分发这些 DLL）。
$vcRuntimes = @('msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_atomic_wait.dll', 'vcruntime140.dll', 'vcruntime140_1.dll')
$vcTarget = Join-Path $stage 'node_modules\sherpa-onnx-win-x64'
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
Copy-Item (Join-Path $PSScriptRoot 'intranet-smoke-test.js') (Join-Path $stage 'smoke-test.js')
Copy-Item (Join-Path $PSScriptRoot 'env-check.js') (Join-Path $stage 'env-check.js')

# ── 5. 压缩 ───────────────────────────────────────────────
$zip = "$stage.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -Force

$stageMb = [math]::Round((Get-ChildItem $stage -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
$zipMb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host "打包完成：$zip"
Write-Host "  解压后 ${stageMb} MB / zip ${zipMb} MB（sherpa-onnx-node 1.12.1 + VC++ 运行库）"
Write-Host "拿走前建议本机先自检：解压 zip 后在解压目录运行  node env-check.js && node smoke-test.js"
