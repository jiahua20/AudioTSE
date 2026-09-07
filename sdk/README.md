# AudioTSE SDK

把 `app/` 原型中验证过的实时目标说话人提取（TSE）链路，封装为可端侧部署的 SDK，并附带最小 Electron demo 验证接入体验。

## 现状

目录刚创建，尚未选定封装路线，二选一后在此展开：

- **TS 直接封装**：TypeScript/Node 层封装 SDK，推理走 ONNX Runtime Web / Node 等前端可达的运行时。
- **C++ 封装**：C++ 完成核心封装（ONNX Runtime / LibTorch / sherpa-onnx C++），Electron 通过 N-API / FFI 桥接调用。

## 背景参考

- `app/README.md` 的「TSE 生产接入」一节：端侧可保持现有 WebSocket 协议，ONNX Runtime/LibTorch 做 TSE、Sherpa-ONNX C++ 做 ASR，Electron 无需改动。
- WeSep 自带的 LibTorch runtime 当前只支持 Linux，Windows 需自行补 CMake 依赖或优先导出 ONNX。
