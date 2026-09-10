// UMD 单文件库（audiotse-gate.umd.js）的类型声明：导出面与 dist/core 完全一致。
// <script> 直引时挂全局变量 AudioTSEGate（export as namespace 让 TS 两边都认识）。
// 本文件由 script/build-umd.js 拷贝到 dist/umd/，随内网包放在 gate/ 下。
export * from '../core/index'
export as namespace AudioTSEGate
