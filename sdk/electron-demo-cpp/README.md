# 声纹门控最小 Electron Demo（C++ SDK）

与 `sdk/electron-demo-web` 界面与交互完全相同的对照版：后端从 web SDK 换成
**C++ SDK（`sdk/cpp`）**，验证 C++ 版在真实麦克风流式场景可用。

架构：主进程 spawn C++ 桥接进程 `sdk\cpp\build\gate_stream.exe`（stdin 二进制帧
喂音频/命令，stdout JSON 行回事件），渲染进程的麦克风采集（AudioWorklet 100ms 块）
经 IPC → 主进程 → 子进程 stdin；事件原路回传。renderer 与 preload 与 web demo
逐字节相同——宿主无感切换后端。

## 运行

前置：模型已由 `app/start.ps1` 下载到 `app/models/`（直接复用）。

```powershell
# 1. 构建 C++ SDK 与桥接进程（首次会自动下载 sherpa-onnx 预编译包）
cd sdk\cpp
.\build.ps1               # 产出 build\gate_stream.exe（dll 已就位）

# 2. 启动 demo（node_modules 已就位，只装了 electron）
cd ..\electron-demo-cpp
npm start
```

用法：点「注册」说**四个字左右**（如「我是本人」），说完自动完成（净语音够
1.2 秒即停）；然后点「开始监听」——目标说话人绿色放行并回放，其他人红色拒绝。

## 离线自检（不用麦克风）

`sdk/web` 里已有对照工具，另有子进程协议级验证（喂样例 wav，与 web SDK 流式
基准逐位对拍）：

```powershell
cd sdk\electron-demo-cpp
node test-gate-stream.js     # PASS = 桥接协议与判定结果和 web 版一致
```

## 结构

```
main/index.js            主进程：spawn gate_stream.exe、stdin/stdout 桥、IPC
preload.js               同 web demo（contextBridge 暴露 window.gateApi）
renderer/                同 web demo（AudioWorklet 采麦、判定展示、放行段排播）
test-gate-stream.js      协议级自检（样例 wav 全链路）
```

- 协议见 `sdk/cpp/examples/gate_stream.cc` 头部注释（'A'音频/'B'注册/'F'收尾/
  'S'监听/'T'停止/'Q'退出；事件 JSON 行）。
- 段音频以 int16 量化回传（回放精度足够，JSON 体积减半）。
- 已知局限同 web demo：无回声消除（可戴耳机）；重叠语音只能整段放行/拒绝。
