# @audiotse/gate-napi —— sdk/cpp 声纹门控 SDK 的 N-API addon 构建根。
# 由 cmake-js 驱动（自动提供 node 头文件与导入库：CMAKE_JS_INC / CMAKE_JS_LIB），
# node-addon-api 头目录由 build.ps1 以 -DNODE_ADDON_API_DIR=<包目录> 传入。
# 源码复用 sdk/cpp（sherpa 依赖经 cmake/sherpa-onnx.cmake 共享导入），产物为
# build/Release/audiotse_gate_napi.node + 同目录 4 个运行时 dll。
cmake_minimum_required(VERSION 3.16)
project(audiotse-gate-napi LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# 源码为 UTF-8（含中文注释/字符串），MSVC 需显式声明，否则按系统 GBK 解析出诡异错误
if(MSVC)
    add_compile_options(/utf-8)
endif()

# sherpa-onnx 预编译包按 MD（动态 CRT）发布，统一 /MD 避免混链接
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")

# 复用 sdk/cpp 的源码、头文件与 sherpa-onnx 预编译包导入
set(AUDIOTSE_CPP_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../cpp")
include("${AUDIOTSE_CPP_ROOT}/cmake/sherpa-onnx.cmake")

# SDK 静态库（与 sdk/cpp 构建里的 audiotse_gate 同一来源，这里静态链进 addon）
add_library(audiotse_gate STATIC
    "${AUDIOTSE_CPP_ROOT}/src/core/embedder.cpp"
    "${AUDIOTSE_CPP_ROOT}/src/core/voice_filter.cpp"
    "${AUDIOTSE_CPP_ROOT}/src/full/speaker_gate.cpp")
target_include_directories(audiotse_gate PUBLIC "${AUDIOTSE_CPP_ROOT}/include")
target_link_libraries(audiotse_gate PUBLIC ${AUDIOTSE_SHERPA_TARGET})

# node-addon-api 头（npm 包，随本包 devDependencies 安装）
if(NOT DEFINED NODE_ADDON_API_DIR)
    message(FATAL_ERROR "缺 -DNODE_ADDON_API_DIR=<node-addon-api 包目录>（build.ps1 自动传入）")
endif()

# N-API addon：MODULE 目标 → .node。NAPI_VERSION=8 兼容 Node 18+/Electron 41。
# win_delay_load_hook：cmake-js 链接 /DELAYLOAD:NODE.EXE，需把符号解析重定向到
# 宿主进程（Electron 下没有 node.exe 模块），否则首次 napi 调用会挂死。
add_library(audiotse_gate_napi MODULE
    napi/addon.cc
    napi/win_delay_load_hook.cc)
set_target_properties(audiotse_gate_napi PROPERTIES
    PREFIX ""
    SUFFIX ".node")
# 单配置生成器（Ninja）默认落在 build/ 根，统一收进 build/<Config>/（与 VS 生成器一致）
if(CMAKE_BUILD_TYPE)
    set_target_properties(audiotse_gate_napi PROPERTIES
        LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/${CMAKE_BUILD_TYPE}")
endif()
target_include_directories(audiotse_gate_napi PRIVATE
    "${AUDIOTSE_CPP_ROOT}/include"
    "${NODE_ADDON_API_DIR}"
    "${CMAKE_JS_INC}")  # node 头文件（node_api.h），由 cmake-js 提供
target_compile_definitions(audiotse_gate_napi PRIVATE NAPI_VERSION=8)
target_link_libraries(audiotse_gate_napi PRIVATE audiotse_gate)
if(WIN32 AND CMAKE_JS_LIB)
    # Windows 下 node.exe/electron.exe 导出 napi 符号，链接 node.lib（cmake-js 提供）；
    # 必须显式 /DELAYLOAD:NODE.EXE（cmake-js 各版本行为不一，不能依赖它传）：
    # 否则 node.exe 进普通导入表，Electron 里加载 DLL 时就去找 node.exe → 加载了
    # PATH 里的系统 Node → 版本错配段错误。配 napi/win_delay_load_hook.cc 把解析
    # 重定向到宿主进程后，Node/Electron 才能通吃。
    target_link_options(audiotse_gate_napi PRIVATE "/DELAYLOAD:NODE.EXE")
    target_link_libraries(audiotse_gate_napi PRIVATE delayimp)
endif()
if(WIN32 AND CMAKE_JS_LIB)
    # Windows 下 node.exe/electron.exe 导出 napi 符号，链接 node.lib（cmake-js 提供）
    target_link_libraries(audiotse_gate_napi PRIVATE "${CMAKE_JS_LIB}")
endif()

if(AUDIOTSE_SHERPA_DLLS)
    # sherpa 运行时 dll 跟到 .node 同目录：libuv dlopen 用 LOAD_WITH_ALTERED_SEARCH_PATH，
    # 会优先在 .node 所在目录解析依赖，宿主无需配 PATH
    add_custom_command(TARGET audiotse_gate_napi POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different ${AUDIOTSE_SHERPA_DLLS} $<TARGET_FILE_DIR:audiotse_gate_napi>)
endif()
