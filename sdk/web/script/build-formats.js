// 交付格式产物构建：web SDK 三种模块格式中，本脚本产出两种——
//   dist/umd/audiotse-gate.umd.js  UMD 单文件（<script> 全局 / AMD / require 兼容）
//   dist/esm/                      多文件 ES Modules（import，Vite/Rollup/webpack 等构建器）
// 第三种 CommonJS（多文件）= npm run build 的 dist/core，交付时拷为 gate/cjs。
// 三者在交付包 gate/ 下按目录区分（cjs/ esm/ umd/），同一套 API，按接法选：
//
//   CommonJS：      const { VoiceFilter } = require('…/gate/cjs')
//   ESM：           import { VoiceFilter } from '…/gate/esm'
//   全局变量：      <script src="…/gate/umd/audiotse-gate.umd.js"></script> → globalThis.AudioTSEGate
//                   （仅限带 require 的环境，如 Electron nodeIntegration 的渲染进程）
//
// 为什么必须有 esm/：Vite/Rollup 等构建器做 ESM 静态分析，纯 CommonJS（exports.X = …）
// 会被判「既无具名导出也无默认导出」直接报错；esm/ 是原生 import/export 语法。注意
// esm/ 内部相对导入不带 .js 后缀（tsc 不改写）——构建器可解析，Node 直跑 ESM 不行。
//
// 关于 CJS 产物里的 __esModule 标记：那是 TS/Babel 把 ESM 编译成 CommonJS 时加的互操作
// 标记（告诉转译器 require 时别再包一层 .default），不代表文件是 ESM——构建器的静态
// 分析也不认它，需要 ESM 就用 esm/。
//
// sherpa-onnx-node 是原生模块（.node + dll），物理上进不了 js 文件，保持 external——
// UMD 运行时用加载方传入的 require 解析；esm/ 里对它的 import 改写为 default 导入再
// 解构（Node 的 CJS 互操作识别不出 sherpa 的具名导出，宿主构建链输出 ESM 时会在运行
// 期解析失败；default 导入对 CJS 永远安全）。与内网项目自带的 sherpa-onnx-node 共用
// 同一运行时，进程内不引入第二份 onnxruntime.dll（两份同名 dll 在 Windows 下按模块名
// 去重会错配，报「无法运行 %1」193）。
//
// 实现说明：esbuild 不支持 --format=umd（只支持 iife/cjs/esm），先打成 CJS 单文件，
// 再包一层标准 UMD 壳（factory 参数注入 require/module/exports，AMD/全局分支自备模块
// 对象）；多文件 ESM 用本包 TypeScript 以 --module esnext 出到 dist/esm/。
// 前置：先 npm run build（生成 dist/core，UMD 的打包输入）。
const path = require('node:path')
const fs = require('node:fs')
const esbuild = require('esbuild')

const root = path.resolve(__dirname, '..')
const entry = path.join(root, 'dist', 'core', 'index.js')
const outDir = path.join(root, 'dist', 'umd')
const outfile = path.join(outDir, 'audiotse-gate.umd.js')

const result = esbuild.buildSync({
  entryPoints: [entry],
  bundle: true,
  format: 'cjs',
  platform: 'node',
  target: 'node18',
  external: ['sherpa-onnx-node'],
  write: false,
  logLevel: 'info',
})
const cjsBody = result.outputFiles[0].text

const umd = `\
/**
 * AudioTSE 声纹门控 SDK（core，无 VAD）— UMD 单文件库
 * 全局变量名：AudioTSEGate（<script> 直引时）
 * 运行时依赖：sherpa-onnx-node（需可 require 到；与宿主项目共用，见交付包 node_modules/）
 * 模型文件（models/sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k.onnx）与接入示例见交付包；API 与 dist/core 完全一致
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    // CommonJS：Node / Electron 主进程 / 各打包器
    factory(require, module, exports)
  } else if (typeof define === 'function' && define.amd) {
    // AMD：需宿主预先把 sherpa-onnx-node 注册为具名模块
    define(['require'], function (rq) {
      var mod = { exports: {} }
      factory(rq, mod, mod.exports)
      return mod.exports
    })
  } else {
    // 全局变量：<script> 直引，需环境自带 require（Electron nodeIntegration 渲染进程）
    var mod = { exports: {} }
    factory(typeof require === 'function' ? require : undefined, mod, mod.exports)
    root.AudioTSEGate = mod.exports
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function (require, module, exports) {
  if (typeof require !== 'function') {
    throw new Error('AudioTSEGate 需要运行环境能解析 sherpa-onnx-node（Node / Electron 主进程，或开启 nodeIntegration 的渲染进程）')
  }
//──────────────────── esbuild 打包的 SDK 主体（CommonJS）────────────────────
${cjsBody}//────────────────────────────────────────────────────────────────────────────
})`

fs.mkdirSync(outDir, { recursive: true })
fs.writeFileSync(outfile, umd)
fs.copyFileSync(path.join(__dirname, 'audiotse-gate.d.ts'), path.join(outDir, 'audiotse-gate.d.ts'))
const kb = (fs.statSync(outfile).size / 1024).toFixed(1)
console.log(`UMD 构建完成：${outfile}（${kb} KB）+ audiotse-gate.d.ts`)

// ── 多文件 ESM（dist/esm/，交付时拷为 gate/esm）──────────────
// 与 dist/core 同结构、原生 import/export（构建器静态分析的接入面）。随后把 sherpa 的
// 具名导入改写为 default 导入再解构：Node 的 CJS 互操作识别不出它的具名导出
// （module.exports 里的间接引用），宿主构建链输出 ESM 时会在运行期解析失败；
// default 导入对 CJS 永远安全。改写带断言：未命中立即报错，防止源码 import
// 形态变化后静默产出坏文件。
const { execFileSync } = require('node:child_process')
const esmOut = path.join(root, 'dist', 'esm')
execFileSync(process.execPath, [
  require.resolve('typescript/bin/tsc'),
  '-p', path.join(root, 'tsconfig.build.json'),
  '--module', 'esnext',
  '--moduleResolution', 'bundler',
  '--outDir', esmOut,
], { stdio: 'inherit' })

const embedderEsm = path.join(esmOut, 'core', 'embedder.js')
const SHERPA_NAMED_IMPORT = "import { SpeakerEmbeddingExtractor } from 'sherpa-onnx-node';"
const embedderText = fs.readFileSync(embedderEsm, 'utf8')
if (!embedderText.includes(SHERPA_NAMED_IMPORT)) {
  throw new Error(`未找到 sherpa 具名导入（源码 import 形态可能已变化）：${embedderEsm}`)
}
fs.writeFileSync(embedderEsm, embedderText.replace(
  SHERPA_NAMED_IMPORT,
  "import __sherpa from 'sherpa-onnx-node';\nconst { SpeakerEmbeddingExtractor } = __sherpa;",
))
const esmCount = fs.readdirSync(path.join(esmOut, 'core')).filter((f) => f.endsWith('.js')).length
console.log(`ESM 构建完成：${path.join(esmOut, 'core')}（多文件 ${esmCount} 个 .js，sherpa 已改 default 导入）`)

// 旧版单文件 ESM 产物已不交付（三目录方案），清掉防误用
fs.rmSync(path.join(outDir, 'audiotse-gate.esm.mjs'), { force: true })
