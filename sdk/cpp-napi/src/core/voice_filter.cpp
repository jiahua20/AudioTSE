// 声纹过滤核心实现：判定语义与 app 后端 / web SDK core/voice-filter.ts 一致。
#include "audiotse/core/voice_filter.hpp"

#include <algorithm>
#include <cmath>
#include <mutex>
#include <stdexcept>
#include <utility>

namespace audiotse {

float Cosine(const std::vector<float> &left, const std::vector<float> &right) {
    float dot = 0.0f, ln = 0.0f, rn = 0.0f;
    const size_t n = std::min(left.size(), right.size());
    for (size_t i = 0; i < n; ++i) {
        dot += left[i] * right[i];
        ln += left[i] * left[i];
        rn += right[i] * right[i];
    }
    const float denominator = std::sqrt(ln) * std::sqrt(rn);
    return denominator > 1e-8f ? dot / denominator : 0.0f;
}

struct VoiceFilter::Impl {
    SpeakerEmbedder embedder;
    /// L2 归一化后的注册声纹；空 = 未注册
    std::vector<float> enrollment;
    /// 放行所需的相似度阈值基准
    float threshold = 0.5f;
    /// 短注册阈值补偿系数
    float short_enroll_factor = 0.7f;
    /// 当前实际生效的放行阈值（短注册补偿后）
    float active_threshold = 0.5f;
    /// 声纹推理串行锁（Enroll/Judge 共用一个推理器）
    std::mutex embed_mutex;

    explicit Impl(const VoiceFilterConfig &config)
        : embedder(MakeEmbedderConfig(config)),  // 模型不可用时抛 std::runtime_error
          threshold(config.threshold),
          short_enroll_factor(config.short_enroll_threshold_factor),
          active_threshold(config.threshold) {}

    static SpeakerEmbedderConfig MakeEmbedderConfig(const VoiceFilterConfig &config) {
        SpeakerEmbedderConfig c;
        c.model = config.speaker_model;
        c.num_threads = config.num_threads;
        return c;
    }
};

VoiceFilter::VoiceFilter(const VoiceFilterConfig &config) : impl_(new Impl(config)) {}

VoiceFilter::~VoiceFilter() = default;

bool VoiceFilter::Enrolled() const { return !impl_->enrollment.empty(); }

float VoiceFilter::EffectiveThreshold() const { return impl_->active_threshold; }

EnrollResult VoiceFilter::Enroll(const float *samples, size_t n) {
    std::vector<float> embedding;
    {
        std::lock_guard<std::mutex> lock(impl_->embed_mutex);
        embedding = impl_->embedder.Embed(samples, n);
    }
    float norm = 0.0f;
    for (float v : embedding) norm += v * v;
    norm = std::sqrt(norm) + 1e-8f;
    for (float &v : embedding) v /= norm;
    impl_->enrollment = std::move(embedding);

    const double speech_seconds = static_cast<double>(n) / kSampleRate;
    impl_->active_threshold =
        speech_seconds < kShortEnrollBoundarySeconds ? impl_->threshold * impl_->short_enroll_factor
                                                     : impl_->threshold;
    return EnrollResult{speech_seconds};
}

JudgeResult VoiceFilter::Judge(const float *samples, size_t n) {
    JudgeResult result;
    result.duration_seconds = static_cast<double>(n) / kSampleRate;
    if (impl_->enrollment.empty()) {
        result.similarity = 1.0f;
        result.accepted = true;
        return result;
    }
    {
        std::lock_guard<std::mutex> lock(impl_->embed_mutex);
        result.similarity = Cosine(impl_->embedder.Embed(samples, n), impl_->enrollment);
    }
    result.accepted = result.similarity >= impl_->active_threshold;
    return result;
}

bool VoiceFilter::Filter(const float *samples, size_t n, JudgeResult *result) {
    const JudgeResult judge = Judge(samples, n);
    if (result != nullptr) *result = judge;
    return judge.accepted;
}

}  // namespace audiotse
