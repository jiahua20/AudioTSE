# 内网离线交付打包脚本：把声纹过滤 SDK（core 入口）运行所需的一切打成一个 zip。
# 产出内容：SDK 产物 + node_modules 运行时闭包 + 声纹模型 + 样例音频 + 离线自检脚本。
#
# 用法（在任意目录）：
#   powershell -File sdk\web\script\package-intranet.ps1             # 全平台 onnxruntime（~284MB）
#   powershell -File sdk\web\script\package-intranet.ps1 -WinOnly   # 只留 Windows 二进制（~40MB）
#
# 前置：sdk\web 下已 npm install 且已 npm run build（脚本会检查）。
param(
    [switch]$WinOnly,
    [string]$OutputDir = ""
)

$ErrorActionPreference = 'Stop'

$webDir = Split-Path $PSScriptRoot -Parent                 # sdk\web
$repoRoot = Split-Path (Split-Path $webDir -Parent) -Parent # 仓库根
$speakerModel = Join-Path $repoRoot 'app\models\sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k\model.onnx'
$samplesDir = Join-Path $repoRoot 'app\samples'

# ── 前置检查 ────────────────────────────────────────────────
$distCore = Join-Path $webDir 'dist\core\index.js'
if (-not (Test-Path $distCore)) { throw "缺 $distCore —— 请先在 sdk\web 下运行 npm run build" }
if (-not (Test-Path (Join-Path $webDir 'node_modules\onnxruntime-node'))) { throw "缺 node_modules —— 请先在 sdk\web 下运行 npm install" }
foreach ($f in @($speakerModel, (Join-Path $samplesDir 'enroll_target.wav'), (Join-Path $samplesDir 'target_clean.wav'), (Join-Path $samplesDir 'other_clean.wav'))) {
    if (-not (Test-Path $f)) { throw "缺文件：$f" }
}

# ── 准备暂存目录 ────────────────────────────────────────────
if (-not $OutputDir) { $OutputDir = Join-Path $PSScriptRoot 'out' }
$stamp = Get-Date -Format 'yyyyMMdd-HHmm'
$stage = Join-Path $OutputDir "audiotse-intranet-$stamp"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path (Join-Path $stage 'gate\dist'), (Join-Path $stage 'gate\examples'), (Join-Path $stage 'models'), (Join-Path $stage 'samples'), (Join-Path $stage 'node_modules') | Out-Null

# ── 1. SDK 产物（core 入口）────────────────────────────────
Copy-Item -Recurse (Join-Path $webDir 'dist\core') (Join-Path $stage 'gate\dist\')
Copy-Item (Join-Path $webDir 'examples\*.js') (Join-Path $stage 'gate\examples\')
# 包级 package.json：require('<包>/gate') 即命中 dist/core
$gatePackageJson = @'
{
  "name": "audiotse-gate",
  "version": "0.1.0",
  "description": "AudioTSE voiceprint gate SDK (core entry, no VAD, intranet delivery)",
  "main": "dist/core/index.js",
  "types": "dist/core/index.d.ts"
}
'@
[IO.File]::WriteAllText((Join-Path $stage 'gate\package.json'), $gatePackageJson)

# ── 2. node_modules 运行时闭包 ─────────────────────────────
# core 只依赖 onnxruntime-node 及其传递依赖；排除开发工具与 full 专用包
$excluded = @('.bin', '.package-lock.json', '.modules.yaml', '.npmrc', '@types', 'typescript', 'tsx', 'esbuild', '@esbuild', 'sherpa-onnx')
Get-ChildItem (Join-Path $webDir 'node_modules') -Force | Where-Object { $excluded -notcontains $_.Name } | ForEach-Object {
    Copy-Item -Recurse $_.FullName (Join-Path $stage "node_modules\$($_.Name)")
}
if ($WinOnly) {
    # 只留 Windows x64 的原生二进制（默认全平台，大屏若是 Linux 别加这个开关）
    Get-ChildItem (Join-Path $stage 'node_modules\onnxruntime-node\bin\napi-v6') -Directory |
        Where-Object { $_.Name -ne 'win32' } | ForEach-Object { Remove-Item $_.FullName -Recurse -Force }
    Get-ChildItem (Join-Path $stage 'node_modules\onnxruntime-node\bin\napi-v6\win32') -Directory |
        Where-Object { $_.Name -ne 'x64' } | ForEach-Object { Remove-Item $_.FullName -Recurse -Force }
}

# ── 3. 模型与样例音频 ─────────────────────────────────────
Copy-Item $speakerModel (Join-Path $stage 'models\speaker.onnx')
Copy-Item (Join-Path $samplesDir 'enroll_target.wav'), (Join-Path $samplesDir 'target_clean.wav'), (Join-Path $samplesDir 'other_clean.wav') (Join-Path $stage 'samples\')

# ── 4. 交付说明与离线自检 ─────────────────────────────────
Copy-Item (Join-Path $PSScriptRoot 'intranet-README.md') (Join-Path $stage 'README.md')
Copy-Item (Join-Path $PSScriptRoot 'intranet-smoke-test.js') (Join-Path $stage 'smoke-test.js')

# ── 5. 压缩 ───────────────────────────────────────────────
$zip = "$stage.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -Force

$stageMb = [math]::Round((Get-ChildItem $stage -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
$zipMb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
$binaryNote = '含全平台二进制'
if ($WinOnly) { $binaryNote = '仅 Windows x64 二进制' }
Write-Host "打包完成：$zip"
Write-Host "  解压后 ${stageMb} MB / zip ${zipMb} MB（$binaryNote）"
Write-Host "拿走前建议本机先自检：解压 zip 后在解压目录运行  node smoke-test.js"
