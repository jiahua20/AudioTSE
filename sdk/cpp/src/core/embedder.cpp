// 声纹提取实现：sherpa-onnx cxx-api 的 SpeakerEmbeddingExtractor（C API 封装，
// 可直接用官方 win-x64 预编译 dll，无需源码编译 sherpa-onnx）。
// 每次 Embed 建独立流，喂完一整段后取声纹；音频太短不足一帧时 sherpa 返回空向量。
#include "audiotse/core/embedder.hpp"

#include <stdexcept>

#include "sherpa-onnx/c-api/cxx-api.h"

namespace audiotse {
namespace {

constexpr int32_t kEmbedderSampleRate = 16000;

// cxx 包装类不可默认构造（MSVC 下无隐式默认构造），只能由 Create() 初始化
sherpa_onnx::cxx::SpeakerEmbeddingExtractor CreateExtractor(const SpeakerEmbedderConfig &config) {
    sherpa_onnx::cxx::SpeakerEmbeddingExtractorConfig c;
    c.model = config.model;
    c.num_threads = config.num_threads;
    c.debug = config.debug;
    c.provider = config.provider;
    return sherpa_onnx::cxx::SpeakerEmbeddingExtractor::Create(c);
}

}  // namespace

struct SpeakerEmbedder::Impl {
    sherpa_onnx::cxx::SpeakerEmbeddingExtractor extractor;

    explicit Impl(const SpeakerEmbedderConfig &config) : extractor(CreateExtractor(config)) {}
};

SpeakerEmbedder::SpeakerEmbedder(const SpeakerEmbedderConfig &config) : impl_(new Impl(config)) {
    if (impl_->extractor.Get() == nullptr) {
        throw std::runtime_error("failed to load speaker model: " + config.model);
    }
}

SpeakerEmbedder::~SpeakerEmbedder() = default;

std::vector<float> SpeakerEmbedder::Embed(const float *samples, size_t n) {
    sherpa_onnx::cxx::OnlineStream stream = impl_->extractor.CreateStream();
    stream.AcceptWaveform(kEmbedderSampleRate, samples, static_cast<int32_t>(n));
    stream.InputFinished();
    std::vector<float> embedding = impl_->extractor.ComputeEmbedding(&stream);
    if (embedding.empty()) {
        throw std::runtime_error("audio too short for one frame");
    }
    return embedding;
}

int32_t SpeakerEmbedder::Dim() const { return impl_->extractor.Dim(); }

}  // namespace audiotse
