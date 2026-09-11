// 内网环境自检（不加载模型也基本能定位问题）：查运行环境架构 → 原生二进制完整性
// → 依赖 DLL 逐个解析 → 加载 + 模型推理。
// 在本包根目录运行：node env-check.js   （或用贵方 Electron 跑：electron env-check.js）
// 「The specified module could not be found」(Windows 126 / ERR_DLOPEN_FAILED) = 缺依赖
// DLL，下方「依赖 DLL 解析」一节会直接列出缺哪个、其余各从哪里解析（📦=包内自带 /
// 系统=Windows 内置）。
const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')
const os = require('node:os')

const binDir = path.join(__dirname, 'node_modules', 'sherpa-onnx-win-x64')

console.log('== 运行环境 ==')
console.log(`platform=${process.platform}  process.arch=${process.arch}  os.arch=${os.arch()}`)
console.log(`node=${process.versions.node}  electron=${process.versions.electron || '（纯 Node）'}`)
if (process.platform !== 'win32' || process.arch !== 'x64') {
  console.log('❌ 本包的原生二进制只有 win32/x64 版：需要 64 位 Windows + x64 的 Node/Electron')
  process.exit(1)
}
console.log('✅ 架构匹配（win32/x64）')

console.log('\n== 二进制完整性（对照官方交付包）==')
// 交付版 sherpa-onnx.node（sherpa-onnx-win-x64 1.12.1）的 SHA256 / 字节数
const EXPECTED_SHA256 = '1FEE25C2F8BB3DF1BF52B9FBCA699E9D4B0CCF7E7F2916BD1772D61BB9821F81'
const EXPECTED_BYTES = 565248
const bindingPath = path.join(binDir, 'sherpa-onnx.node')
if (!fs.existsSync(bindingPath)) {
  console.log(`❌ 找不到 ${bindingPath}`)
  process.exit(1)
}
const buf = fs.readFileSync(bindingPath)
const sha256 = crypto.createHash('sha256').update(buf).digest('hex').toUpperCase()
console.log(`sherpa-onnx.node：${buf.length} 字节  SHA256=${sha256}`)
if (sha256 !== EXPECTED_SHA256 || buf.length !== EXPECTED_BYTES) {
  console.log('❌ 与交付版不一致 → 文件在传输/解压/入库过程中损坏。')
  console.log('   排查：① 用官方 zip 重新解压（换 7-Zip/WinRAR 试）；② 若 node_modules 是走 git 传的，')
  console.log('   二进制会被换行转换破坏——改为传 zip，或对该目录关闭 text/eol 转换并用 LFS。')
  process.exit(1)
}
console.log('✅ 与交付版逐字节一致')

// ── 依赖 DLL 解析：解析 PE 导入表，逐个报告解析来源 ──────────
/** 解析 PE（x64）静态导入表，返回 DLL 名列表 */
function peImports(pe) {
  const eLfanew = pe.readUInt32LE(0x3c)
  const optOff = eLfanew + 24
  const magic = pe.readUInt16LE(optOff)
  const dataDirOff = magic === 0x20b ? optOff + 112 : optOff + 96
  const importRva = pe.readUInt32LE(dataDirOff + 8) // 数据目录[1] = 导入表
  if (!importRva) return []
  const numSections = pe.readUInt16LE(eLfanew + 6)
  const secOff = optOff + pe.readUInt16LE(eLfanew + 20)
  const sections = []
  for (let i = 0; i < numSections; i++) {
    const s = secOff + i * 40
    sections.push({ va: pe.readUInt32LE(s + 12), vsize: pe.readUInt32LE(s + 8), raw: pe.readUInt32LE(s + 20) })
  }
  const rva2off = (rva) => {
    for (const s of sections) if (rva >= s.va && rva < s.va + s.vsize) return rva - s.va + s.raw
    return null
  }
  const names = []
  let desc = rva2off(importRva)
  while (desc) {
    const nameRva = pe.readUInt32LE(desc + 12)
    if (!nameRva) break
    const off = rva2off(nameRva)
    let end = off
    while (pe[end] !== 0) end++
    names.push(pe.toString('ascii', off, end).toLowerCase())
    desc += 20
  }
  return names
}

console.log('\n== 依赖 DLL 解析（📦=包内同目录 / API set=系统内置 / 系统=System32）==')
let missing = false
for (const file of ['sherpa-onnx.node', 'sherpa-onnx-c-api.dll', 'sherpa-onnx-cxx-api.dll', 'onnxruntime.dll']) {
  const file_ = path.join(binDir, file)
  if (!fs.existsSync(file_)) {
    console.log(`  ❌ 缺失 ${file}（本应随包内置）`)
    missing = true
    continue
  }
  for (const dll of peImports(fs.readFileSync(file_))) {
    if (fs.existsSync(path.join(binDir, dll))) {
      console.log(`  📦 ${dll}（${file} 的依赖，包内自带）`)
    } else if (dll.startsWith('api-ms-') || dll.startsWith('ext-ms-')) {
      console.log(`  API set ${dll}（${file}）—— Windows 10+ 内置`)
    } else if (fs.existsSync(path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', dll))) {
      console.log(`  系统 ${dll}（${file}）—— System32 提供`)
    } else {
      console.log(`  ❌ 缺失 ${dll}（${file} 的依赖）—— 请用官方最新交付包（已内置 VC++ 运行库）`)
      missing = true
    }
  }
}
if (missing) process.exit(1)
console.log('✅ 全部依赖可解析（VC++ 运行库已随包内置，无需在机器上安装任何运行库）')

console.log('\n== 原生模块加载 + 模型推理 ==')
try {
  const { SpeakerEmbeddingExtractor } = require(path.join(__dirname, 'node_modules', 'sherpa-onnx-node'))
  const extractor = new SpeakerEmbeddingExtractor({
    model: path.join(__dirname, 'models', 'sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k.onnx'),
    numThreads: 1,
    provider: 'cpu',
    debug: 0,
  })
  console.log(`✅ sherpa-onnx-node 加载成功，声纹模型就绪（dim=${extractor.dim}）—— 可继续 node smoke-test.js`)
} catch (e) {
  console.log(`❌ 加载失败：${e.message}`)
  console.log('   若上方依赖全部 ✅ 仍失败：检查杀毒/安全软件是否拦截了 .node/.dll；')
  console.log('   若宿主项目进程里已加载其它 onnxruntime.dll（版本过旧），确认项目自带 sherpa-onnx-node 与本包同版本')
  process.exit(1)
}
