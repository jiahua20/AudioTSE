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

# sherpa-onnx 依赖，二选一（默认 A）：
#   A. 官方 win-x64 预编译包（含 include/ lib/ bin/），目录名保留官方发布全名
#      （如 sherpa-onnx-v1.13.7-win-x64-shared-MD-Release，版本一目了然）：
#      - AUDIOTSE_SHERPA_ROOT 不传时自动探测 third_party/sherpa-onnx-*
#        （build.ps1 会下载解压官方包到这，不改名）
#      - 手动传 -DAUDIOTSE_SHERPA_ROOT=<包目录>
#   B. 已安装 sherpa-onnx（make install）：-Dsherpa-onnx_DIR=<prefix>/lib/cmake/sherpa-onnx
set(AUDIOTSE_SHERPA_ROOT "" CACHE PATH "sherpa-onnx 官方预编译包根目录（含 include/ lib/ bin/）")

set(AUDIOTSE_SHERPA_TARGET)
set(AUDIOTSE_SHERPA_DLLS)

if(AUDIOTSE_SHERPA_ROOT STREQUAL "")
    # 自动探测：取字典序最大的 sherpa-onnx-* 目录（新版包字典序更大）
    file(GLOB _sherpa_candidates "${CMAKE_CURRENT_SOURCE_DIR}/third_party/sherpa-onnx-*")
    if(_sherpa_candidates)
        list(SORT _sherpa_candidates ORDER DESCENDING)
        list(GET _sherpa_candidates 0 AUDIOTSE_SHERPA_ROOT)
        message(STATUS "使用 sherpa-onnx 预编译包: ${AUDIOTSE_SHERPA_ROOT}")
    endif()
endif()

if(NOT AUDIOTSE_SHERPA_ROOT STREQUAL "")
    if(NOT EXISTS "${AUDIOTSE_SHERPA_ROOT}/lib/sherpa-onnx-cxx-api.lib")
        message(FATAL_ERROR "AUDIOTSE_SHERPA_ROOT 下没有 lib/sherpa-onnx-cxx-api.lib: ${AUDIOTSE_SHERPA_ROOT}")
    endif()
    # 预编译包只发布 cxx-api（C API 的 C++ 封装），内部 csrc 库未随包分发
    add_library(sherpa-onnx-cxx-api SHARED IMPORTED)
    set_target_properties(sherpa-onnx-cxx-api PROPERTIES
        IMPORTED_LOCATION "${AUDIOTSE_SHERPA_ROOT}/lib/sherpa-onnx-cxx-api.dll"
        IMPORTED_IMPLIB "${AUDIOTSE_SHERPA_ROOT}/lib/sherpa-onnx-cxx-api.lib"
        INTERFACE_INCLUDE_DIRECTORIES "${AUDIOTSE_SHERPA_ROOT}/include")
    add_library(onnxruntime SHARED IMPORTED)
    set_target_properties(onnxruntime PROPERTIES
        IMPORTED_LOCATION "${AUDIOTSE_SHERPA_ROOT}/lib/onnxruntime.dll"
        IMPORTED_IMPLIB "${AUDIOTSE_SHERPA_ROOT}/lib/onnxruntime.lib")
    target_link_libraries(sherpa-onnx-cxx-api INTERFACE onnxruntime)
    set(AUDIOTSE_SHERPA_TARGET sherpa-onnx-cxx-api)
    set(AUDIOTSE_SHERPA_DLLS
        "${AUDIOTSE_SHERPA_ROOT}/lib/sherpa-onnx-cxx-api.dll"
        "${AUDIOTSE_SHERPA_ROOT}/lib/sherpa-onnx-c-api.dll"
        "${AUDIOTSE_SHERPA_ROOT}/lib/onnxruntime.dll"
        "${AUDIOTSE_SHERPA_ROOT}/lib/onnxruntime_providers_shared.dll")
else()
    find_package(sherpa-onnx REQUIRED)
    set(AUDIOTSE_SHERPA_TARGET sherpa-onnx-core)
endif()

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
