# C++ 声纹门控 SDK

silero VAD 切段 + 3D-Speaker ER2Net 声纹相似度门控，VAD 与声纹提取**直接使用
sherpa-onnx 原生库**（与 `app/` 的 Python 后端同引擎、同参数、同特征前端），无需
自己移植 fbank。API 与 `sdk/web` 的 TS 版语义一一对应。

## 状态

源码就绪，**本机未编译验证**（当前机器无 MSVC/cmake 工具链）。按 sherpa-onnx
v1.13.x 的 C++ API 编写，如版本间 API 有变化以对应版本头文件为准。

## 构建

前置：CMake ≥ 3.16、VS 2019+（MSVC）、sherpa-onnx。

```powershell
# 方式 A：先安装 sherpa-onnx（可从其 release 取 win-x64 预编译库，或源码 make install）
cd sdk\cpp
cmake -B build -A x64 -Dsherpa-onnx_DIR=<sherpa安装前缀>\lib\cmake\sherpa-onnx
cmake --build build --config Release

# 方式 B：让 CMake 自动拉取 sherpa-onnx 源码一起构建（首次耗时长）
cmake -B build -A x64 -DAUDIOTSE_FETCH_SHERPA=ON
cmake --build build --config Release
```

运行示例（在仓库根目录，模型路径写死指向 `app/models/`）：

```powershell
.\sdk\cpp\build\Release\gate_demo.exe app\samples\enroll_target.wav app\samples\mixed.wav
```

## API

```cpp
audiotse::SpeakerGateConfig config;
config.vad_model = "silero_vad.onnx";
config.speaker_model = "3dspeaker_erres2net.onnx";
config.threshold = 0.5f;              // 与 app 后端一致

audiotse::SpeakerGate gate(config);
gate.Enroll(enroll_samples);          // 注册：整段 float32 [-1,1] @16k

for (每 100ms 音频 chunk) {
    for (const auto& seg : gate.AcceptWaveform(chunk)) {
        // seg.similarity / seg.accepted / seg.samples / seg.start
        if (seg.accepted) 播放或送 ASR(seg.samples);
    }
}
```

## 后续（TSE 接入时）

保持同一 `AcceptWaveform` 流式接口，被拒绝/接受段改送 WeSep BSRNN（ONNX 导出）
做真分离，宿主代码无感切换。参见 `app/README.md`「TSE 生产接入」。
