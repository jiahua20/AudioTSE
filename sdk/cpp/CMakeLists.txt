cmake_minimum_required(VERSION 3.16)
project(audiotse-gate LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# 源码为 UTF-8（含中文注释/字符串），MSVC 需显式声明，否则按系统 GBK 解析出诡异错误
if(MSVC)
    add_compile_options(/utf-8)
endif()

# sherpa-onnx 预编译包按 MD（动态 CRT）发布，统一 /MD 避免混链接
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")

# sherpa-onnx 依赖（探测/导入逻辑与 sdk/cpp-napi 共享，见 cmake/sherpa-onnx.cmake；
# 二选一：默认 A 官方 win-x64 预编译包自动探测 third_party/sherpa-onnx-* 或
# -DAUDIOTSE_SHERPA_ROOT=<包目录>；B 已安装包 -Dsherpa-onnx_DIR=<prefix>/lib/cmake/sherpa-onnx）
include(${CMAKE_CURRENT_SOURCE_DIR}/cmake/sherpa-onnx.cmake)

# SDK 静态库（core：无 VAD 声纹过滤；full：VAD 切段 + 门控）
add_library(audiotse_gate STATIC
    src/core/embedder.cpp
    src/core/voice_filter.cpp
    src/full/speaker_gate.cpp)
target_include_directories(audiotse_gate PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(audiotse_gate PUBLIC ${AUDIOTSE_SHERPA_TARGET})

# CLI 示例（在仓库根目录运行，模型路径见源码默认值）
add_executable(gate_demo examples/gate_demo.cc)
target_link_libraries(gate_demo PRIVATE audiotse_gate)

# 流式桥接进程：stdin 二进制帧 / stdout JSON 行，Electron/Node 宿主以子进程接入 C++ SDK
add_executable(gate_stream examples/gate_stream.cc)
target_link_libraries(gate_stream PRIVATE audiotse_gate)

if(AUDIOTSE_SHERPA_DLLS)
    # 运行时 dll 直接跟到产物目录，宿主无需配 PATH
    foreach(_target gate_demo gate_stream)
        add_custom_command(TARGET ${_target} POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E copy_if_different ${AUDIOTSE_SHERPA_DLLS} $<TARGET_FILE_DIR:${_target}>)
    endforeach()
endif()
