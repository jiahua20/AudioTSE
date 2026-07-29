# AudioTSE

CPU 优先的实时目标说话人提取桌面原型。Electron/Vue 负责麦克风与字幕界面，Python WebSocket 服务负责编排模型和 Sherpa-ONNX 中文流式 ASR。

> 想从头理解数据流与原理（TSE / 重叠相加 / 声纹门控降级等），读 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 技术栈

- 桌面端：Electron、Vue 3、TypeScript、Vite
- 后端：Python 3.10、WebSocket
- 中文 ASR：Sherpa-ONNX Streaming Zipformer 14M INT8
- 音频格式：16 kHz、单声道、PCM16

## 当前能力边界

- 中文流式 ASR：可切换 Sherpa-ONNX Zipformer 14M 中文或 Paraformer 中英双语 INT8。
- 目标声音注册与实时协议：可运行，注册音频保存在会话内存中。
- 声纹门控：使用 Silero VAD + 3D-Speaker ER2Net，根据注册声纹只转写相似说话人，适合轮流发言。
- 纯音频 TSE（主路径）：已接入可选的 WeSep BSRNN + ECAPA VoxCeleb 权重，直接使用注册语音从混合波形提取目标 PCM，再送入 ASR。
- 原音直通：不筛选说话人，用于比较不同 ASR 的识别效果。

当前 WeSep 权重约 262 MB，使用英语 VoxCeleb 数据训练，BSRNN 为 online（因果）变体。适配器已改为 1 秒分块推理，并叠加积压追赶与 VAD 门控（无人说话时跳过 BSRNN、直接原声直通），首字延时约 1.7 秒；仍属于可验证效果的 CPU 实验模式，中文泛化、CPU RTF 和窗口边界听感仍需实测。声纹门控只是 TSE 不可用时的降级方案：多人同时说话时，它不能把混合波形中的目标声音分离出来，也可能发生误收或漏收。

## 一键启动

运行前需要安装：

- Node.js 18.20 或更高版本
- Miniconda 或 Anaconda
- Windows PowerShell

首次使用先创建 Python 环境：

```powershell
conda create -n AudioTSE python=3.10 -y
```

然后在项目根目录运行：

```powershell
.\start.ps1
```

脚本会自动检查 `AudioTSE` Conda 环境、安装缺失依赖，准备 Zipformer、Paraformer、Silero VAD 和 3D-Speaker 模型，然后启动 Python 后端和 Electron/Vue 桌面端。关闭桌面端或按 `Ctrl+C` 后，脚本启动的后端也会退出。

模型文件体积较大，不包含在 Git 仓库中，首次启动时会下载到本地 `models/` 目录。

默认启动只准备轻量的 ASR/VAD/声纹模型。单独安装实验 TSE（会安装 CPU PyTorch、WeSep 并下载约 262 MB 权重）：

```powershell
.\scripts\install-wesep-tse.ps1
```

安装完成后重新运行 `start.ps1`，界面的“纯音频 TSE（实验）”会变为可选，并优先作为默认处理模式。

如果 PowerShell 阻止本地脚本，可使用：

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

只检查和准备依赖而不启动应用：

```powershell
.\start.ps1 -SetupOnly
```

## 目录结构

```text
backend/                 Python WebSocket、会话状态和 ASR
docs/                    架构与原理文档（ARCHITECTURE.md / TSE-DEEP-DIVE.md）
electron/                Electron 主进程与 preload
scripts/                 模型下载、TSE 安装与测试音频脚本
src/                     Vue 桌面界面
start.ps1                Windows 一键启动脚本
```

`node_modules/`、`dist/`、`models/`、Python 缓存和本机编辑器配置均被 Git 忽略。

## TSE 生产接入

当前 `BufferedWeSepTse` 已完成链路验证并落地分块实时化：注册 PCM 和每个 1 秒混合音频窗进入 WeSep，输出目标 PCM 后再进入 `StreamingAsr`；叠加积压追赶（积压超过窗口+容忍量时丢弃最旧、只分离最新窗，保证实时流延迟有界）与 VAD 门控（静音窗跳过 BSRNN 直接原声直通）。下一步应测试中文重叠语音的 SI-SDR/可懂度和 CPU RTF，再决定是进一步缩短窗口，还是训练小型因果 SpEx+/SpeakerBeam。盲分离后做声纹选路只能作为近似 TSE 实验，不能替代参考语音条件模型。

端侧部署可继续保持同一协议：ONNX Runtime/LibTorch 完成 TSE，Sherpa-ONNX C++ 完成 ASR，Electron 无需改动。WeSep 自带的 LibTorch runtime 当前只支持 Linux，Windows 版本需要自行补 CMake/依赖适配或优先导出 ONNX。