// gate_stream：C++ SDK 的流式桥接进程，供 Electron/Node 宿主以子进程方式使用
// C++ 声纹门控 SDK（麦克风实时流场景）。
//
//   stdin  二进制小端帧：1 字节命令 + 可选 4 字节长度 + 负载
//     'A' + u32 N + N×float32  喂 100ms 量级音频块 [-1,1] @16k（注册/监听自动路由）
//     'B'                      开始流式注册
//     'F'                      提前结束注册
//     'S'                      开始监听
//     'T'                      停止监听（冲出尾部段）
//     'Q'                      退出
//   stdout JSON 行（UTF-8，\n 结尾）：
//     {"type":"ready","threshold":0.5}
//     {"type":"enrollProgress","speechSeconds":1.20,"enough":true}
//     {"type":"enrolled","speechSeconds":1.40,"effectiveThreshold":0.5}
//     {"type":"segment","start":123,"durationSeconds":2.10,"similarity":0.601,
//      "accepted":true,"samples":[int16, ...]}   // 未注册时 similarity 为 null
//     {"type":"error","message":"..."}
//   stderr 原样输出 sherpa/运行日志。
//
// 用法：gate_stream <silero_vad.onnx> <speaker.onnx> [threshold=0.5]
// 与 electron-demo-web 的 IPC 事件语义一一对应（renderer 无需感知后端是 web 还是 C++）。
#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include <fcntl.h>
#include <io.h>

#include "audiotse/full/speaker_gate.hpp"

namespace {

// JSON 字符串转义（控制字符/引号/反斜杠；中文按 UTF-8 原样输出即合法）
std::string JsonEscape(const std::string &s) {
    std::string out;
    out.reserve(s.size() + 8);
    for (char c : s) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (static_cast<unsigned char>(c) < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", c);
                    out += buf;
                } else {
                    out += c;
                }
        }
    }
    return out;
}

void Emit(const std::string &json_line) {
    std::fputs(json_line.c_str(), stdout);
    std::fputc('\n', stdout);
    std::fflush(stdout);
}

void EmitError(const std::string &message) {
    Emit("{\"type\":\"error\",\"message\":\"" + JsonEscape(message) + "\"}");
}

/** 读满 n 字节；stdin 关闭/中断返回 false。 */
bool ReadExact(void *buf, size_t n) {
    return std::fread(buf, 1, n, stdin) == n;
}

class GateStream {
public:
    explicit GateStream(const audiotse::SpeakerGateConfig &config) : gate_(config) {}

    void Run() {
        Emit("{\"type\":\"ready\"}");
        char cmd = 0;
        while (ReadExact(&cmd, 1)) {
            switch (cmd) {
                case 'A': {
                    uint32_t n = 0;
                    if (!ReadExact(&n, 4) || n == 0 || n > 16000000) return;
                    std::vector<char> bytes(static_cast<size_t>(n) * 4);
                    if (!ReadExact(bytes.data(), bytes.size())) return;
                    // 小端 float32（x86/x64 主机序）
                    OnAudio(reinterpret_cast<const float *>(bytes.data()), n);
                    break;
                }
                case 'B':
                    gate_.BeginEnroll();
                    enrolling_ = true;
                    break;
                case 'F':
                    if (enrolling_) FinishEnroll();
                    break;
                case 'S':
                    monitoring_ = true;
                    break;
                case 'T':
                    if (monitoring_) {
                        monitoring_ = false;
                        EmitSegments(gate_.Flush());
                    }
                    break;
                case 'Q':
                    return;
                default:
                    EmitError("unknown command");
                    return;
            }
        }
    }

private:
    void OnAudio(const float *samples, size_t n) {
        if (enrolling_) {
            // 注册模式：内部剥静音累计净语音，够量自动完成（与 web demo 主进程一致，
            // 音频只进注册 VAD，不进主流）
            audiotse::EnrollProgress progress = gate_.EnrollChunk(samples, n);
            char line[128];
            std::snprintf(line, sizeof(line),
                          "{\"type\":\"enrollProgress\",\"speechSeconds\":%.2f,\"enough\":%s}",
                          progress.speech_seconds, progress.enough ? "true" : "false");
            Emit(line);
            if (progress.enough) FinishEnroll();
            return;
        }
        if (monitoring_) EmitSegments(gate_.AcceptWaveform(samples, n));
    }

    void FinishEnroll() {
        enrolling_ = false;
        try {
            const audiotse::EnrollResult result = gate_.FinishEnroll();
            char line[160];
            std::snprintf(line, sizeof(line),
                          "{\"type\":\"enrolled\",\"speechSeconds\":%.2f,\"effectiveThreshold\":%.3f}",
                          result.speech_seconds, gate_.EffectiveThreshold());
            Emit(line);
        } catch (const std::exception &e) {
            EmitError(e.what());
        }
    }

    void EmitSegments(const std::vector<audiotse::GateSegmentEvent> &events) {
        for (const audiotse::GateSegmentEvent &event : events) {
            // int16 量化回传（回放精度足够，JSON 体积减半）
            std::string json;
            json.reserve(event.samples.size() * 7 + 128);
            char head[192];
            std::snprintf(head, sizeof(head),
                          "{\"type\":\"segment\",\"start\":%lld,\"durationSeconds\":%.2f,",
                          static_cast<long long>(event.start), event.duration_seconds);
            json += head;
            if (event.has_similarity) {
                char sim[48];
                std::snprintf(sim, sizeof(sim), "\"similarity\":%.3f,", event.similarity);
                json += sim;
            } else {
                json += "\"similarity\":null,";
            }
            json += event.accepted ? "\"accepted\":true," : "\"accepted\":false,";
            json += "\"samples\":[";
            for (size_t i = 0; i < event.samples.size(); ++i) {
                float v = event.samples[i];
                if (v > 1.0f) v = 1.0f;
                if (v < -1.0f) v = -1.0f;
                json += std::to_string(static_cast<int>(std::lroundf(v * 32767.0f)));
                if (i + 1 < event.samples.size()) json += ',';
            }
            json += "]}";
            Emit(json);
        }
    }

    audiotse::SpeakerGate gate_;
    bool enrolling_ = false;
    bool monitoring_ = false;
};

}  // namespace

int main(int argc, char **argv) {
    if (argc < 3) {
        std::fprintf(stderr, "用法: gate_stream <silero_vad.onnx> <speaker.onnx> [threshold=0.5]\n");
        return 1;
    }
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);

    audiotse::SpeakerGateConfig config;
    config.vad_model = argv[1];
    config.speaker_model = argv[2];
    if (argc > 3) config.threshold = std::strtof(argv[3], nullptr);

    try {
        GateStream stream(config);
        stream.Run();
    } catch (const std::exception &e) {
        EmitError(std::string("init failed: ") + e.what());
        return 1;
    }
    return 0;
}
