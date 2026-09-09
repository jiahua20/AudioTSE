// 声纹门控完整版实现：sherpa-onnx cxx-api 的 VoiceActivityDetector（段式）切流，
// 声纹注册/判定复用 core 的 VoiceFilter；注册音频经独立 VAD 剥静音后再提声纹。
#include "audiotse/full/speaker_gate.hpp"

#include <algorithm>
#include <cstdio>
#include <optional>
#include <stdexcept>
#include <utility>

#include "sherpa-onnx/c-api/cxx-api.h"

namespace audiotse {
namespace {

constexpr float kVadBufferSizeSeconds = 30;

// sherpa 官方预编译库对大块单次喂入会丢失段边界（实测 5s 语音整段喂只检出 0.16s，
// WASM 版同样问题），统一切成 160ms 小块喂入，使行为与调用方块大小无关
// （与 web SDK 的 vad.ts 同一对策）。
class SegmentedVad {
public:
    static SegmentedVad Create(const SpeakerGateConfig &config) {
        sherpa_onnx::cxx::VadModelConfig vad_config;
        vad_config.silero_vad.model = config.vad_model;
        vad_config.silero_vad.threshold = config.vad_threshold;
        vad_config.silero_vad.min_silence_duration = config.min_silence_duration;
        vad_config.silero_vad.min_speech_duration = config.min_speech_duration;
        vad_config.silero_vad.max_speech_duration = config.max_speech_duration;
        vad_config.silero_vad.window_size = 512;
        vad_config.sample_rate = kSampleRate;
        vad_config.num_threads = config.vad_num_threads;
        vad_config.provider = "cpu";
        vad_config.debug = false;
        sherpa_onnx::cxx::VoiceActivityDetector vad =
            sherpa_onnx::cxx::VoiceActivityDetector::Create(vad_config, kVadBufferSizeSeconds);
        if (vad.Get() == nullptr) {
            throw std::runtime_error("failed to load vad model: " + config.vad_model);
        }
        return SegmentedVad(std::move(vad));
    }

    void AcceptWaveform(const float *samples, size_t n) {
        for (size_t off = 0; off < n; off += kFeedChunkSamples) {
            const size_t m = std::min(kFeedChunkSamples, n - off);
            vad_.AcceptWaveform(samples + off, static_cast<int32_t>(m));
        }
    }

    void Flush() { vad_.Flush(); }
    void Reset() { vad_.Reset(); }
    bool IsEmpty() const { return vad_.IsEmpty(); }
    sherpa_onnx::cxx::SpeechSegment Front() const { return vad_.Front(); }
    void Pop() { vad_.Pop(); }

private:
    explicit SegmentedVad(sherpa_onnx::cxx::VoiceActivityDetector &&vad) : vad_(std::move(vad)) {}

    static constexpr size_t kFeedChunkSamples = 2560;  // 160 ms @16k
    sherpa_onnx::cxx::VoiceActivityDetector vad_;
};

}  // namespace

struct SpeakerGate::Impl {
    SpeakerGateConfig config;
    SegmentedVad vad;
    VoiceFilter filter;
    /// 注册专用 VAD（与主流独立，剥静音用）；空 = 当前没有进行中的注册
    std::optional<SegmentedVad> enroll_vad;
    /// 注册累计的纯语音段
    std::vector<std::vector<float>> enroll_chunks;
    size_t enroll_speech_samples = 0;

    explicit Impl(const SpeakerGateConfig &c)
        : config(c), vad(SegmentedVad::Create(c)), filter(MakeFilterConfig(c)) {}

    static VoiceFilterConfig MakeFilterConfig(const SpeakerGateConfig &c) {
        VoiceFilterConfig fc;
        fc.speaker_model = c.speaker_model;
        fc.threshold = c.threshold;
        fc.short_enroll_threshold_factor = c.short_enroll_threshold_factor;
        fc.num_threads = c.embed_num_threads;
        return fc;
    }

    /// 把注册 VAD 中已完结的语音段搬进 enroll_chunks
    void CollectEnrollSegments() {
        while (!enroll_vad->IsEmpty()) {
            sherpa_onnx::cxx::SpeechSegment segment = enroll_vad->Front();
            enroll_vad->Pop();
            enroll_chunks.push_back(std::move(segment.samples));
            enroll_speech_samples += enroll_chunks.back().size();
        }
    }

    /// 取走主流 VAD 中已完结的语音段并逐段判定
    std::vector<GateSegmentEvent> Drain() {
        std::vector<GateSegmentEvent> events;
        while (!vad.IsEmpty()) {
            sherpa_onnx::cxx::SpeechSegment segment = vad.Front();
            vad.Pop();

            GateSegmentEvent event;
            event.start = segment.start;
            event.duration_seconds =
                static_cast<float>(segment.samples.size()) / static_cast<float>(kSampleRate);
            const JudgeResult judge = filter.Judge(segment.samples.data(), segment.samples.size());
            if (filter.Enrolled()) {
                event.similarity = judge.similarity;
                event.has_similarity = true;
            }
            event.accepted = judge.accepted;
            event.samples = std::move(segment.samples);
            events.push_back(std::move(event));
        }
        return events;
    }
};

// SegmentedVad::Create 在 VAD 模型加载失败时抛 std::runtime_error
SpeakerGate::SpeakerGate(const SpeakerGateConfig &config) : impl_(new Impl(config)) {}

SpeakerGate::~SpeakerGate() = default;

bool SpeakerGate::Enrolled() const { return impl_->filter.Enrolled(); }

float SpeakerGate::EffectiveThreshold() const { return impl_->filter.EffectiveThreshold(); }

EnrollResult SpeakerGate::Enroll(const float *samples, size_t n) {
    BeginEnroll();
    EnrollChunk(samples, n);
    return FinishEnroll();
}

void SpeakerGate::BeginEnroll() {
    impl_->enroll_vad = SegmentedVad::Create(impl_->config);
    impl_->enroll_chunks.clear();
    impl_->enroll_speech_samples = 0;
}

EnrollProgress SpeakerGate::EnrollChunk(const float *samples, size_t n) {
    if (!impl_->enroll_vad) BeginEnroll();
    impl_->enroll_vad->AcceptWaveform(samples, n);
    impl_->CollectEnrollSegments();
    const double speech_seconds = static_cast<double>(impl_->enroll_speech_samples) / kSampleRate;
    return EnrollProgress{speech_seconds,
                          speech_seconds >= impl_->config.enroll_target_speech_seconds};
}

EnrollResult SpeakerGate::FinishEnroll() {
    if (!impl_->enroll_vad) {
        throw std::runtime_error("注册未开始（先 BeginEnroll）");
    }
    impl_->enroll_vad->Flush();
    impl_->CollectEnrollSegments();
    impl_->enroll_vad.reset();

    const double speech_seconds = static_cast<double>(impl_->enroll_speech_samples) / kSampleRate;
    if (speech_seconds < impl_->config.enroll_min_speech_seconds) {
        char message[128];
        std::snprintf(message, sizeof(message), "净语音不足：%.2fs < %.2gs，请再说一句", speech_seconds,
                      impl_->config.enroll_min_speech_seconds);
        throw std::runtime_error(message);
    }
    // VoiceFilter.Enroll 负责声纹提取与短注册阈值补偿（输入已是剥好静音的纯语音）
    size_t total = 0;
    for (const std::vector<float> &chunk : impl_->enroll_chunks) total += chunk.size();
    std::vector<float> speech;
    speech.reserve(total);
    for (const std::vector<float> &chunk : impl_->enroll_chunks) {
        speech.insert(speech.end(), chunk.begin(), chunk.end());
    }
    return impl_->filter.Enroll(speech);
}

std::vector<GateSegmentEvent> SpeakerGate::AcceptWaveform(const float *samples, size_t n) {
    impl_->vad.AcceptWaveform(samples, n);
    return impl_->Drain();
}

std::vector<GateSegmentEvent> SpeakerGate::Flush() {
    impl_->vad.Flush();
    return impl_->Drain();
}

void SpeakerGate::Reset() { impl_->vad.Reset(); }

}  // namespace audiotse
