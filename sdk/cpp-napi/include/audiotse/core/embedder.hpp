// 声纹提取器：sherpa-onnx 原生 SpeakerEmbeddingExtractor（3D-Speaker ER2Net ONNX，
// kaldi fbank 前端由 sherpa 内部完成），与 app 的 Python 后端同引擎同特征配置。
// 对应 web SDK 的 core/embedder.ts。
#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace audiotse {

struct SpeakerEmbedderConfig {
    /// 3D-Speaker ER2Net onnx 路径
    std::string model;
    /// 推理线程数（模型小，默认 2）
    int32_t num_threads = 2;
    std::string provider = "cpu";
    bool debug = false;
};

class SpeakerEmbedder {
public:
    /// 加载声纹模型；不是合法的声纹模型时抛 std::runtime_error
    explicit SpeakerEmbedder(const SpeakerEmbedderConfig &config);
    ~SpeakerEmbedder();

    SpeakerEmbedder(const SpeakerEmbedder &) = delete;
    SpeakerEmbedder &operator=(const SpeakerEmbedder &) = delete;

    /// 一段音频（float [-1,1] @16k）→ 定长声纹向量（未归一化）。
    /// 音频太短不足一帧时抛 std::runtime_error。
    std::vector<float> Embed(const float *samples, size_t n);
    std::vector<float> Embed(const std::vector<float> &samples) {
        return Embed(samples.data(), samples.size());
    }

    /// 声纹维度
    int32_t Dim() const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace audiotse
