# C++ 声纹门控 SDK

silero VAD 切段 + 3D-Speaker ER2Net 声纹相似度门控，VAD 与声纹提取**直接使用
sherpa-onnx 原生库**（与 `app/` 的 Python 后端同引擎、同参数、同特征前端），无需
自己移植 fbank。API 与 `sdk/web` 的 TS 版语义一一对应：

| web SDK | 本 SDK | 说明 |
| --- | --- | --- |
| `core/embedder.ts` SpeakerEmbedder | `audiotse/core/embedder.hpp` | 声纹提取（sherpa 原生，fbank 前端内置） |
| `core/voice-filter.ts` VoiceFilter | `audiotse/core/voice_filter.hpp` | 无 VAD 版：Enroll/Judge/Filter + 短注册阈值补偿 |
| `full/vad.ts` + `full/speaker-gate.ts` | `audiotse/full/speaker_gate.hpp` | VAD 切段门控 + 流式注册 |

判定语义与 app 后端 / web SDK 完全一致：

- 阈值基准 0.5，短注册（净语音 <1.5s）自动降为 `0.5 × 0.7`
- 注册音频经独立 VAD 剥静音后再提声纹（静音不参与特征统计）
- 流式注册累计净语音到 1.2s 即够，低于 0.6s 时 `FinishEnroll()` 抛错
- 未注册时所有段放行

## 构建

前置：VS 2019+（含 C++ 与 CMake/Ninja 组件）。sherpa-onnx 用官方 win-x64 预编译包，
`build.ps1` 首次运行会自动下载解压到 `third_party/`（目录保留官方发布全名，
如 `sherpa-onnx-v1.13.7-win-x64-shared-MD-Release`，版本一目了然）：

```powershell
cd sdk\cpp
.\build.ps1                 # Release 构建（VS 环境 → 下载 sherpa-onnx → cmake+ninja）
```

也可手动构建（sherpa-onnx 二选一）：

```powershell
# 方式 A：预编译包（build.ps1 已下载到 third_party\sherpa-onnx-*，CMake 自动探测）
cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release

# 方式 B：已安装 sherpa-onnx（make install），配置时传 -Dsherpa-onnx_DIR=<prefix>\lib\cmake\sherpa-onnx
```

# 方式 C：源码拉取并连同构建（首次耗时长）
cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DAUDIOTSE_FETCH_SHERPA=ON
```

## 运行示例

在仓库根目录，模型路径写死指向 `app/models/`（运行所需 dll 已在构建时拷贝到 `build\`）：

```powershell
.\sdk\cpp\build\gate_demo.exe app\samples\enroll_target.wav app\samples\mixed.wav
```

## API

完整版（VAD 切段 + 门控，对应 web `full` 入口）：

```cpp
audiotse::SpeakerGateConfig config;
config.vad_model = "silero_vad.onnx";
config.speaker_model = "3dspeaker_erres2net.onnx";
config.threshold = 0.5f;              // 与 app 后端一致

audiotse::SpeakerGate gate(config);
gate.Enroll(enroll_samples);          // 批式注册：内部剥静音，<1.5s 自动阈值补偿

gate.BeginEnroll();                   // 或流式注册：边说边收，够 1.2s 自动完成
auto progress = gate.EnrollChunk(chunk);
if (progress.enough) gate.FinishEnroll();   // 净语音 <0.6s 抛 std::runtime_error

for (每 100ms 音频 chunk) {
    for (const auto& seg : gate.AcceptWaveform(chunk)) {
        // seg.similarity / seg.accepted / seg.samples / seg.start
        if (seg.accepted) 播放或送 ASR(seg.samples);
    }
}
for (const auto& seg : gate.Flush()) { /* 尾部段 */ }
```

core 版（外部 VAD 已切好段，对应 web `core` 入口）：

```cpp
audiotse::VoiceFilterConfig fc;
fc.speaker_model = "3dspeaker_erres2net.onnx";
audiotse::VoiceFilter filter(fc);

filter.Enroll(enroll_samples);        // 一段完整语音注册
auto judge = filter.Judge(seg_samples);   // {similarity, accepted, duration_seconds}
if (filter.Filter(seg_samples)) 回传(seg_samples);  // 便捷过滤
```

## 后续（TSE 接入时）

保持同一 `AcceptWaveform` 流式接口，被拒绝/接受段改送 WeSep BSRNN（ONNX 导出）
做真分离，宿主代码无感切换。参见 `app/README.md`「TSE 生产接入」。

> Node/Electron 宿主请优先用 N-API 封装 `sdk/cpp-napi`（进程内 require 直载，
> API 与 web 版同名），子进程桥接 `gate_stream.exe` 保留给非 Node 宿主。
