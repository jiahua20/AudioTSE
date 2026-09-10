// 声纹提取实现：sherpa-onnx C API 的 SpeakerEmbeddingExtractor。
// （sherpa 1.12.1 的 cxx-api 尚无 SpeakerEmbeddingExtractor 包装，1.13 起才有，
//   为与内网 sherpa-onnx-node 1.12.1 同运行时，这里直接用 C API。）
// 每次 Embed 建独立流，喂完一整段后取声纹；音频太短不足一帧时返回空向量转异常。
#include "audiotse/core/embedder.hpp"

#include <stdexcept>

#include "sherpa-onnx/c-api/c-api.h"

namespace audiotse {
namespace {

constexpr int32_t kEmbedderSampleRate = 16000;

}  // namespace

struct SpeakerEmbedder::Impl {
    const SherpaOnnxSpeakerEmbeddingExtractor *extractor = nullptr;
    // C API 的 config 只存 const char* 指针，字符串必须由本对象持有保活
    std::string model;
    std::string provider;

    explicit Impl(const SpeakerEmbedderConfig &config) : model(config.model), provider(config.provider) {
        SherpaOnnxSpeakerEmbeddingExtractorConfig c{};
        c.model = model.c_str();
        c.num_threads = config.num_threads;
        c.debug = config.debug ? 1 : 0;
        c.provider = provider.c_str();
        extractor = SherpaOnnxCreateSpeakerEmbeddingExtractor(&c);
        if (extractor == nullptr) {
            throw std::runtime_error("failed to load speaker model: " + config.model);
        }
    }

    ~Impl() {
        if (extractor != nullptr) {
            SherpaOnnxDestroySpeakerEmbeddingExtractor(extractor);
        }
    }
};

SpeakerEmbedder::SpeakerEmbedder(const SpeakerEmbedderConfig &config) : impl_(new Impl(config)) {}

SpeakerEmbedder::~SpeakerEmbedder() = default;

std::vector<float> SpeakerEmbedder::Embed(const float *samples, size_t n) {
    const SherpaOnnxOnlineStream *stream =
        SherpaOnnxSpeakerEmbeddingExtractorCreateStream(impl_->extractor);
    if (stream == nullptr) {
        throw std::runtime_error("create embedding stream failed");
    }
    SherpaOnnxOnlineStreamAcceptWaveform(stream, kEmbedderSampleRate, samples,
                                         static_cast<int32_t>(n));
    SherpaOnnxOnlineStreamInputFinished(stream);

    const float *embedding =
        SherpaOnnxSpeakerEmbeddingExtractorComputeEmbedding(impl_->extractor, stream);
    std::vector<float> out;
    if (embedding != nullptr) {
        const int32_t dim = SherpaOnnxSpeakerEmbeddingExtractorDim(impl_->extractor);
        out.assign(embedding, embedding + dim);
        SherpaOnnxSpeakerEmbeddingExtractorDestroyEmbedding(embedding);
    }
    SherpaOnnxDestroyOnlineStream(stream);

    if (out.empty()) {
        throw std::runtime_error("audio too short for one frame");
    }
    return out;
}

int32_t SpeakerEmbedder::Dim() const {
    return SherpaOnnxSpeakerEmbeddingExtractorDim(impl_->extractor);
}

}  // namespace audiotse
