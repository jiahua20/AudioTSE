// @audiotse/gate-napi 的 JS 包装：把 N-API addon 包装成与 @audiotse/gate（sdk/web）
// 完全同名同签名的双入口 API（SpeakerGate 完整版 / VoiceFilter core 版），
// 宿主代码可在 web 与 napi 两个后端间无感切换。
//
// 关键点：
// - .node 与 4 个 sherpa dll 同目录（build.ps1 拷贝；内网交付包为 native/ 布局）；
//   require 前把该目录注入 PATH，兜底非标准加载场景（libuv dlopen 本身已优先
//   搜索 .node 所在目录）。
// - C++ SDK 要求串行调用（主 VAD/注册 VAD/声纹推理共享状态）：所有异步操作排入
//   同一条 promise 链，保证跨调用的顺序与互斥。
// - 推理在 libuv 工作线程执行（addon 内 AsyncWorker），不阻塞 Electron 主线程。
// - sherpa 运行时为 1.12.1，与内网 sherpa-onnx-node 同版本：进程内 dll 按名去重
//   后是同一版本，任何加载顺序都无冲突。
const fs = require('node:fs')
const path = require('node:path')

const ADDON_NAME = 'audiotse_gate_napi.node'

function resolveAddonPath() {
  const candidates = [
    process.env.AUDIOTSE_GATE_NAPI_PATH,
    path.join(__dirname, 'build', 'Release', ADDON_NAME),
    path.join(__dirname, 'build', 'Debug', ADDON_NAME),
    path.join(__dirname, 'build', ADDON_NAME),
    path.join(__dirname, 'native', ADDON_NAME), // 内网交付包布局（package-intranet-napi.ps1）
  ].filter(Boolean)
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate
  }
  throw new Error(
    `找不到 ${ADDON_NAME}：先在 sdk/cpp-napi 运行 npm install && .\\build.ps1，` +
      `或用 AUDIOTSE_GATE_NAPI_PATH 指定路径。尝试过：${candidates.join('、')}`,
  )
}

const addonPath = resolveAddonPath()
const addonDir = path.dirname(addonPath)
process.env.PATH = addonDir + path.delimiter + (process.env.PATH || '')
const addon = require(addonPath)

/** 固定 16 kHz 单声道（与 C++ SDK kSampleRate 一致） */
const SAMPLE_RATE = 16000

function requireFloat32Array(samples) {
  if (!(samples instanceof Float32Array)) {
    throw new TypeError('音频参数必须是 Float32Array（[-1,1] @16k 单声道）')
  }
  return samples
}

class SpeakerGate {
  /** @type {import('./index.d').GateNative | null} */
  #gate = null
  #disposed = false
  /** @type {Promise<unknown>} 所有异步操作的串行链（SDK 要求串行调用） */
  #queue = Promise.resolve()

  /**
   * 加载模型并创建实例（模型加载在工作线程执行，约 0.5s）。
   * @param {import('./index.d').SpeakerGateConfig} config
   */
  static async create(config) {
    const gate = new SpeakerGate()
    gate.#gate = await addon.createGate(config)
    return gate
  }

  /** 是否已有注册声纹（未注册时所有段放行）。 */
  get enrolled() {
    this.#assertAlive()
    return this.#gate.enrolled
  }

  /** 当前实际生效的放行阈值（短注册补偿后）。 */
  get effectiveThreshold() {
    this.#assertAlive()
    return this.#gate.effectiveThreshold
  }

  /** 开始一次流式注册：复位累计状态（排队执行，保证与在途调用串行）。 */
  beginEnroll() {
    this.#run(() => this.#gate.beginEnroll())
  }

  /** 便捷注册：整段音频内部剥静音后提声纹（= begin + chunk + finish）。 */
  async enroll(samples) {
    requireFloat32Array(samples)
    this.#run(() => this.#gate.beginEnroll())
    await this.#run(() => this.#gate.enrollChunk(requireFloat32Array(samples)))
    return this.#run(() => this.#gate.finishEnroll())
  }

  /** 喂入一段注册音频，返回净语音进度；enough=true 表示已达目标（宿主应 finishEnroll）。 */
  async enrollChunk(samples) {
    requireFloat32Array(samples)
    return this.#run(() => this.#gate.enrollChunk(requireFloat32Array(samples)))
  }

  /** 结束注册并提声纹；净语音低于下限时 reject（注册状态保持未完成）。 */
  async finishEnroll() {
    return this.#run(() => this.#gate.finishEnroll())
  }

  /** 喂入一段流式音频，返回自此完结的语音段及其门控判定。 */
  async accept(samples) {
    requireFloat32Array(samples)
    return this.#run(() => this.#gate.accept(requireFloat32Array(samples)))
  }

  /** 流结束：冲出尾部未完结的语音段并判定。 */
  async flush() {
    return this.#run(() => this.#gate.flush())
  }

  /** 复位 VAD 与段状态（注册声纹保留）。 */
  reset() {
    this.#run(() => this.#gate.reset())
  }

  /** 释放原生资源（排到队列末尾，之前的在途调用完成后执行）。 */
  dispose() {
    this.#disposed = true
    this.#queue = this.#queue.then(
      () => this.#gate && this.#gate.dispose(),
      () => this.#gate && this.#gate.dispose(),
    )
  }

  #assertAlive() {
    if (!this.#gate) throw new Error('gate 已 dispose 或未完成 create')
  }

  /**
   * 把操作排入串行链并返回本次操作的 promise。
   * 前一操作失败不阻断后续排队（各调用独立决议），fire-and-forget 的同步风格
   * 方法（beginEnroll/reset）由调用处吞掉 rejection，错误会在后续 await 调用上暴露。
   */
  #run(job) {
    if (this.#disposed) {
      return Promise.reject(new Error('gate 已 dispose'))
    }
    const result = this.#queue.then(job, job)
    this.#queue = result.then(
      () => undefined,
      () => undefined,
    )
    return result
  }
}

// ── VoiceFilter：core 版（无 VAD），API 与 sdk/web 的 core/voice-filter.ts 同签名 ──
class VoiceFilter {
  /** @type {import('./index.d').FilterNative | null} */
  #filter = null
  #disposed = false
  /** @type {Promise<unknown>} 所有异步操作的串行链（SDK 要求串行调用） */
  #queue = Promise.resolve()

  /**
   * 加载声纹模型并创建实例（模型加载在工作线程执行，约 0.5s）。
   * @param {import('./index.d').VoiceFilterConfig} config
   */
  static async create(config) {
    const filter = new VoiceFilter()
    filter.#filter = await addon.createFilter(config)
    return filter
  }

  /** 是否已注册（未注册时 judge 全部 accepted）。 */
  get enrolled() {
    this.#assertAlive()
    return this.#filter.enrolled
  }

  /** 当前实际生效的放行阈值（短注册补偿后）。 */
  get effectiveThreshold() {
    this.#assertAlive()
    return this.#filter.effectiveThreshold
  }

  /** 注册目标说话人：一段完整语音（float32 [-1,1] @16k，外部 VAD 已切好）。 */
  async enroll(samples) {
    requireFloat32Array(samples)
    return this.#run(() => this.#filter.enroll(requireFloat32Array(samples)))
  }

  /** 判定一段语音是否目标说话人。 */
  async judge(samples) {
    requireFloat32Array(samples)
    return this.#run(() => this.#filter.judge(requireFloat32Array(samples)))
  }

  /**
   * 便捷过滤：目标说话人的语音**原样返回**（同一引用，不拷贝），非目标返回 null；
   * 未注册时原样返回（全放行）。
   */
  async filter(samples) {
    const result = await this.judge(samples)
    return result.accepted ? samples : null
  }

  /** 释放原生资源（排到队列末尾，之前的在途调用完成后执行）。 */
  dispose() {
    this.#disposed = true
    this.#queue = this.#queue.then(
      () => this.#filter && this.#filter.dispose(),
      () => this.#filter && this.#filter.dispose(),
    )
  }

  #assertAlive() {
    if (!this.#filter) throw new Error('filter 已 dispose 或未完成 create')
  }

  #run(job) {
    if (this.#disposed) {
      return Promise.reject(new Error('filter 已 dispose'))
    }
    const result = this.#queue.then(job, job)
    this.#queue = result.then(
      () => undefined,
      () => undefined,
    )
    return result
  }
}

module.exports = { SpeakerGate, VoiceFilter, SAMPLE_RATE, addonPath }
