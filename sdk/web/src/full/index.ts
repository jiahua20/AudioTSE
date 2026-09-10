// 完整版入口：VAD 切段 + 声纹门控。声纹内核来自同包 ../core。
export {
  SpeakerGate,
  SAMPLE_RATE,
  type SpeakerGateConfig,
  type GateSegmentEvent,
  type EnrollProgress,
  type EnrollResult,
} from './speaker-gate'
// 核心能力与类型透传（full 用户可直接用 core 的 VoiceFilter）
export {
  VoiceFilter,
  cosine,
  SpeakerEmbedder,
  computeFbank,
  type VoiceFilterConfig,
  type JudgeResult,
  type FbankOptions,
} from '../core/index'
export { createSileroVad, DEFAULT_VAD_OPTIONS, type SileroVadOptions, type Vad } from './vad'
