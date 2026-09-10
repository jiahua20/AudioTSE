// 把 dist/core 打成 UMD 单文件库（dist/umd/audiotse-gate.umd.js）：
// 给无法走 npm install / 自有构建管线编不过 TypeScript 的宿主（内网等）直接引一个 js。
//
//   CommonJS：      const { VoiceFilter } = require('…/audiotse-gate.umd.js')
//   全局变量：      <script src="…/audiotse-gate.umd.js"></script> → globalThis.AudioTSEGate
//                   （仅限带 require 的环境，如 Electron nodeIntegration 的渲染进程）
//
// sherpa-onnx-node 是原生模块（.node + dll），物理上进不了 js 文件，保持 external——
// 运行时用加载方传入的 require 解析；与内网项目自带的 sherpa-onnx-node 共用同一
// 运行时，进程内不引入第二份 onnxruntime.dll（这正是从 onnxruntime-node 迁移过来的
// 原因：两份同名 dll 在 Windows 下按模块名去重会错配，报「无法运行 %1」193）。
//
// 实现说明：esbuild 不支持 --format=umd（只支持 iife/cjs/esm），这里先打成 CJS 单文件，
// 再包一层标准 UMD 壳（factory 参数注入 require/module/exports，AMD/全局分支自备模块对象）。
// 前置：先 npm run build（生成 dist/core）。
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
 * 模型文件（speaker.onnx）与接入示例见交付包；API 与 dist/core 完全一致
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
