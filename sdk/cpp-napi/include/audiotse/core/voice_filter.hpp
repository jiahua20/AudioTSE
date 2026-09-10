// 声纹过滤核心（无 VAD 版，内网场景）：外部 VAD 已把语音切好成段，
// 这里只做「注册声纹 → 每段判定/过滤」：
//   Enroll(samples)   注册目标说话人（一段完整语音）
//   Judge(samples)    判定一段语音是否目标说话人（返回相似度 + accepted）
//   Filter(samples)   便捷过滤：放行返回 true，拦截返回 false
// 与 web SDK 的 core/voice-filter.ts 语义一一对应。
#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "audiotse/core/embedder.hpp"

namespace audiotse {

/// 采样率固定 16 kHz（对应 web SDK 的 SAMPLE_RATE）
constexpr int32_t kSampleRate = 16000;

/// 净语音低于该秒数时启用短注册阈值补偿
constexpr double kShortEnrollBoundarySeconds = 1.5;

struct VoiceFilterConfig {
    /// 3D-Speaker ER2Net onnx 路径
    std::string speaker_model;
    /// 放行所需的相似度阈值基准，默认 0.5（与 app 后端一致）
    float threshold = 0.5f;
    /// 短注册阈值补偿系数：注册语音 < 1.5s 时实际阈值 = threshold × 该系数（默认 0.7）。
    /// 短注册的目标段相似度整体下移（实测 1s 注册下限 ~0.48，5s 注册 ~0.59），阈值
    /// 不降会误拒贴线目标段。设为 1 可禁用自适应。
    float short_enroll_threshold_factor = 0.7f;
    /// 声纹推理线程数
    int32_t num_threads = 2;
};

/// 一段语音的判定结果
struct JudgeResult {
    /// 与注册声纹的余弦相似度；未注册时为 1.0
    float similarity = 1.0f;
    /// 是否目标说话人；未注册时恒为 true
    bool accepted = true;
    /// 输入语音时长（秒）
    double duration_seconds = 0.0;
};

/// 注册结果
struct EnrollResult {
    /// 用于声纹的语音时长（秒）。core 版不做静音剥离，等于输入时长
    double speech_seconds = 0.0;
};

/// 两个向量的余弦相似度（与 app 后端 SpeakerEmbedder.cosine 同实现）
float Cosine(const std::vector<float> &left, const std::vector<float> &right);

class VoiceFilter {
public:
    /// 加载声纹模型（ER2Net ONNX，CPU 推理）
    explicit VoiceFilter(const VoiceFilterConfig &config);
    ~VoiceFilter();

    VoiceFilter(const VoiceFilter &) = delete;
    VoiceFilter &operator=(const VoiceFilter &) = delete;

    /// 是否已注册（未注册时 Judge 全部 accepted）
    bool Enrolled() const;

    /// 当前实际生效的放行阈值（短注册补偿后，未注册时等于基准阈值）
    float EffectiveThreshold() const;

    /// 注册目标说话人：一段完整语音（float [-1,1] @16k，外部 VAD 已切好，
    /// 建议净语音 ≥1s/四个字；<1.5s 自动启用阈值补偿）
    EnrollResult Enroll(const float *samples, size_t n);
    EnrollResult Enroll(const std::vector<float> &samples) {
        return Enroll(samples.data(), samples.size());
    }

    /// 判定一段语音是否目标说话人
    JudgeResult Judge(const float *samples, size_t n);
    JudgeResult Judge(const std::vector<float> &samples) {
        return Judge(samples.data(), samples.size());
    }

    /// 便捷过滤：目标说话人的语音放行（音频仍留在调用方缓冲，无需拷贝），
    /// 非目标拦截。返回是否放行；未注册时恒放行。
    /// result 不为空时回填判定详情，适合「把主讲人的语音挑出来回传」的内网场景。
    bool Filter(const float *samples, size_t n, JudgeResult *result = nullptr);
    bool Filter(const std::vector<float> &samples, JudgeResult *result = nullptr) {
        return Filter(samples.data(), samples.size(), result);
    }

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace audiotse
