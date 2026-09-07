// AudioTSE 声纹门控 C++ SDK：silero VAD 切段 + 3D-Speaker ER2Net 声纹相似度门控。
// VAD 与声纹提取直接使用 sherpa-onnx 原生库（与 app 的 Python 后端同引擎同参数），
// 推理依赖 ONNX Runtime 由 sherpa-onnx 自带。
#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace audiotse {

struct SpeakerGateConfig {
    /// silero_vad.onnx 路径
    std::string vad_model;
    /// 3D-Speaker ER2Net onnx 路径
    std::string speaker_model;
    /// 放行所需的相似度阈值（默认 0.5，与 app 后端一致）
    float threshold = 0.5f;

    // ── VAD 参数（与 app 后端 speaker_gate.py 一致）──
    /// 语音概率阈值
    float vad_threshold = 0.5f;
    /// 判定段结束所需最短静音（秒）
    float min_silence_duration = 0.3f;
    /// 成段所需最短语音（秒）
    float min_speech_duration = 0.1f;
    /// 单段最长语音（秒）
    float max_speech_duration = 15.0f;
    /// VAD 线程数（声纹提取固定 2 线程，模型小）
    int32_t num_threads = 1;
};

struct GateSegment {
    /// 段起点在整条流中的采样序号
    int64_t start = 0;
    /// 段时长（秒）
    float duration_seconds = 0.0f;
    /// 与注册声纹的余弦相似度；未注册时无意义（has_similarity=false，全放行）
    float similarity = 1.0f;
    bool has_similarity = false;
    bool accepted = false;
    /// 段内音频（float [-1,1] @16k），宿主可自行播放/送 ASR
    std::vector<float> samples;
};

class SpeakerGate {
public:
    explicit SpeakerGate(const SpeakerGateConfig &config);
    ~SpeakerGate();

    SpeakerGate(const SpeakerGate &) = delete;
    SpeakerGate &operator=(const SpeakerGate &) = delete;

    /// 是否已有注册声纹（未注册时所有段放行）
    bool Enrolled() const;

    /// 注册目标说话人：整段音频（float [-1,1] @16k）→ 归一化声纹
    void Enroll(const float *samples, size_t n);
    void Enroll(const std::vector<float> &samples) { Enroll(samples.data(), samples.size()); }

    /// 喂入流式音频，返回自此完结的语音段及其门控判定
    std::vector<GateSegment> AcceptWaveform(const float *samples, size_t n);
    std::vector<GateSegment> AcceptWaveform(const std::vector<float> &samples) {
        return AcceptWaveform(samples.data(), samples.size());
    }

    /// 流结束：冲出尾部未完结的语音段并判定
    std::vector<GateSegment> Flush();

    /// 复位 VAD 与段状态（注册声纹保留）
    void Reset();

private:
    std::vector<GateSegment> Drain();

    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace audiotse
