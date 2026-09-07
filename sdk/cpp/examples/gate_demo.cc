// 最小 CLI 示例：注册一段 wav，然后流式喂另一段 wav，打印每段门控判定。
// 用法：gate_demo <enroll.wav> <stream.wav> [threshold]
// wav 需为 16 kHz 单声道 PCM16。
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <string>
#include <vector>

#include "audiotse/speaker_gate.hpp"

namespace {

// 极简 RIFF/wav 读取（16 kHz 单声道 PCM16），仅演示用
std::vector<float> ReadWav(const std::string &path) {
    std::ifstream file(path, std::ios::binary);
    if (!file) {
        std::fprintf(stderr, "无法打开 %s\n", path.c_str());
        std::exit(1);
    }
    char header[44];
    file.read(header, 44);
    if (file.gcount() != 44) {
        std::fprintf(stderr, "wav 头不完整: %s\n", path.c_str());
        std::exit(1);
    }
    const uint32_t data_size = *reinterpret_cast<const uint32_t *>(header + 40);
    std::vector<int16_t> pcm(data_size / 2);
    file.read(reinterpret_cast<char *>(pcm.data()), data_size);
    std::vector<float> samples(pcm.size());
    for (size_t i = 0; i < pcm.size(); ++i) samples[i] = pcm[i] / 32768.0f;
    return samples;
}

}  // namespace

int main(int argc, char **argv) {
    if (argc < 3) {
        std::fprintf(stderr, "用法: gate_demo <enroll.wav> <stream.wav> [threshold(默认0.5)]\n");
        return 1;
    }

    audiotse::SpeakerGateConfig config;
    config.vad_model = "app/models/silero_vad/silero_vad.onnx";
    config.speaker_model =
        "app/models/sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k/model.onnx";
    if (argc > 3) config.threshold = std::strtof(argv[3], nullptr);

    audiotse::SpeakerGate gate(config);
    const std::vector<float> enroll = ReadWav(argv[1]);
    gate.Enroll(enroll);
    std::printf("注册完成（%.1f s）\n", enroll.size() / 16000.0f);

    const std::vector<float> stream = ReadWav(argv[2]);
    constexpr size_t kChunk = 1600;  // 100 ms 流式喂入
    std::vector<audiotse::GateSegment> events;
    for (size_t offset = 0; offset < stream.size(); offset += kChunk) {
        const size_t n = std::min(kChunk, stream.size() - offset);
        std::vector<audiotse::GateSegment> chunk_events = gate.AcceptWaveform(stream.data() + offset, n);
        events.insert(events.end(), chunk_events.begin(), chunk_events.end());
    }
    std::vector<audiotse::GateSegment> tail = gate.Flush();
    events.insert(events.end(), tail.begin(), tail.end());

    for (const audiotse::GateSegment &event : events) {
        std::printf("[%.2fs +%.2fs] 相似度 %.3f → %s\n", event.start / 16000.0, event.duration_seconds,
                    event.similarity, event.accepted ? "放行" : "拒绝");
    }
    return 0;
}
