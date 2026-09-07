// 声纹门控实现：sherpa-onnx 原生 VoiceActivityDetector（段式）+
// SpeakerEmbeddingExtractor，判定语义与 app 后端 / web SDK 一致。
#include "audiotse/speaker_gate.hpp"

#include <algorithm>
#include <cmath>
#include <utility>

#include "sherpa-onnx/csrc/silero-vad-model-config.h"
#include "sherpa-onnx/csrc/speaker-embedding-extractor.h"
#include "sherpa-onnx/csrc/vad-model-config.h"
#include "sherpa-onnx/csrc/voice-activity-detector.h"

namespace audiotse {
namespace {

constexpr int32_t kSampleRate = 16000;

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

}  // namespace

struct SpeakerGate::Impl {
    std::unique_ptr<sherpa_onnx::VoiceActivityDetector> vad;
    std::unique_ptr<sherpa_onnx::SpeakerEmbeddingExtractor> extractor;
    std::vector<float> enrollment;  // L2 归一化后的注册声纹
    float threshold = 0.5f;

    std::vector<float> Embed(const float *samples, size_t n) {
        auto stream = extractor->CreateStream();
        stream->AcceptWaveform(kSampleRate, samples, static_cast<int32_t>(n));
        return extractor->Compute(stream.get());
    }
};

SpeakerGate::SpeakerGate(const SpeakerGateConfig &config) : impl_(new Impl) {
    sherpa_onnx::VadModelConfig vad_config;
    vad_config.silero_vad.model = config.vad_model;
    vad_config.silero_vad.threshold = config.vad_threshold;
    vad_config.silero_vad.min_silence_duration = config.min_silence_duration;
    vad_config.silero_vad.min_speech_duration = config.min_speech_duration;
    vad_config.silero_vad.max_speech_duration = config.max_speech_duration;
    vad_config.sample_rate = kSampleRate;
    vad_config.num_threads = config.num_threads;
    vad_config.provider = "cpu";
    vad_config.debug = false;
    impl_->vad = std::make_unique<sherpa_onnx::VoiceActivityDetector>(vad_config, /*buffer_size_in_seconds=*/30);

    sherpa_onnx::SpeakerEmbeddingExtractorConfig embed_config;
    embed_config.model = config.speaker_model;
    embed_config.num_threads = 2;
    embed_config.provider = "cpu";
    embed_config.debug = false;
    impl_->extractor = std::make_unique<sherpa_onnx::SpeakerEmbeddingExtractor>(embed_config);

    impl_->threshold = config.threshold;
}

SpeakerGate::~SpeakerGate() = default;

bool SpeakerGate::Enrolled() const { return !impl_->enrollment.empty(); }

void SpeakerGate::Enroll(const float *samples, size_t n) {
    std::vector<float> embedding = impl_->Embed(samples, n);
    float norm = 0.0f;
    for (float v : embedding) norm += v * v;
    norm = std::sqrt(norm) + 1e-8f;
    for (float &v : embedding) v /= norm;
    impl_->enrollment = std::move(embedding);
}

std::vector<GateSegment> SpeakerGate::AcceptWaveform(const float *samples, size_t n) {
    impl_->vad->AcceptWaveform(std::vector<float>(samples, samples + n));
    return Drain();
}

std::vector<GateSegment> SpeakerGate::Flush() {
    impl_->vad->Flush();
    return Drain();
}

void SpeakerGate::Reset() { impl_->vad->Reset(); }

std::vector<GateSegment> SpeakerGate::Drain() {
    std::vector<GateSegment> events;
    while (!impl_->vad->IsEmpty()) {
        const sherpa_onnx::SpeechSegment &segment = impl_->vad->Front();
        impl_->vad->Pop();

        GateSegment event;
        event.start = segment.start;
        event.duration_seconds = static_cast<float>(segment.samples.size()) / kSampleRate;
        if (Enrolled()) {
            std::vector<float> embedding = impl_->Embed(segment.samples.data(), segment.samples.size());
            event.similarity = Cosine(embedding, impl_->enrollment);
            event.has_similarity = true;
            event.accepted = event.similarity >= impl_->threshold;
        } else {
            event.has_similarity = false;
            event.accepted = true;  // 未注册全放行
        }
        event.samples = segment.samples;
        events.push_back(std::move(event));
    }
    return events;
}

}  // namespace audiotse
