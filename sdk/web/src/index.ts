// 包主入口 = 完整版（VAD + 声纹门控）。
// 无 VAD 的核心版（内网：外部 VAD 已切好段）走子路径入口 '@audiotse/gate/core'
// （= src/core，见该目录 voice-filter.ts）。
export * from './full/index'
