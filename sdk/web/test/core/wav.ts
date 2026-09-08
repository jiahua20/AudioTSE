// 极简 RIFF/wav 读取（16 kHz 单声道 PCM16），core 测试自用——core 包运行时
// 不依赖 sherpa-onnx，测试也不应引入。
import * as fs from 'node:fs'

export function readWavPcm16Mono16k(file: string): Float32Array {
  const buf = fs.readFileSync(file)
  if (buf.readUInt32LE(0) !== 0x46464952) throw new Error('not RIFF: ' + file) // "RIFF"
  let offset = 12 // 跳过 RIFF 头
  let data: Buffer | null = null
  let channels = 0
  let sampleRate = 0
  while (offset + 8 <= buf.length) {
    const id = buf.toString('ascii', offset, offset + 4)
    const size = buf.readUInt32LE(offset + 4)
    const body = buf.subarray(offset + 8, offset + 8 + size)
    if (id === 'fmt ') {
      channels = body.readUInt16LE(2)
      sampleRate = body.readUInt32LE(4)
      const bits = body.readUInt16LE(14)
      if (bits !== 16) throw new Error('expect 16-bit: ' + file)
    } else if (id === 'data') {
      data = body
    }
    offset += 8 + size + (size % 2) // chunk 按 2 字节对齐
  }
  if (!data || channels !== 1 || sampleRate !== 16000) {
    throw new Error(`expect 16k mono pcm16, got ${sampleRate}Hz ${channels}ch: ${file}`)
  }
  const pcm = new Int16Array(data.buffer, data.byteOffset, Math.floor(data.length / 2))
  const samples = new Float32Array(pcm.length)
  for (let i = 0; i < pcm.length; i++) samples[i] = pcm[i] / 32768
  return samples
}
