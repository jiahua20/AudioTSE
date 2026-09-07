export {
  SpeakerGate,
  SAMPLE_RATE,
  cosine,
  type SpeakerGateConfig,
  type GateSegmentEvent,
  type EnrollProgress,
  type EnrollResult,
} from './speaker-gate'
export { SpeakerEmbedder } from './embedder'
export { computeFbank, type FbankOptions } from './fbank'
export { createSileroVad, DEFAULT_VAD_OPTIONS, type SileroVadOptions, type Vad } from './vad'
