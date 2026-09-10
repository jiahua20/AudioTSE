# sherpa-onnx 预编译包的探测与导入（sdk/cpp-napi 自包含构建）。
# include 本文件后可用两个变量：
#   AUDIOTSE_SHERPA_TARGET  应链接的 CMake target 名
#   AUDIOTSE_SHERPA_DLLS    随产物分发的运行时 dll 列表（POST_BUILD 拷贝用）
if(AUDIOTSE_SHERPA_INCLUDED)
    return()
endif()
set(AUDIOTSE_SHERPA_INCLUDED ON)

# 本文件位于 sdk/cpp-napi/cmake/ 下，SDK 根目录（third_party/ 在这里）取上一级
get_filename_component(AUDIOTSE_NAPI_ROOT "${CMAKE_CURRENT_LIST_DIR}/.." ABSOLUTE)

# sherpa-onnx 依赖，二选一（默认 A）：
#   A. 官方 win-x64 预编译包（含 include/ lib/），目录名保留官方发布全名
#      （如 sherpa-onnx-v1.12.1-win-x64-shared，版本一目了然）：
#      - AUDIOTSE_SHERPA_ROOT 不传时自动探测 third_party/sherpa-onnx-*
#        （首次需手动放置/解压官方包到这，不改名）
#      - 手动传 -DAUDIOTSE_SHERPA_ROOT=<包目录>
#      版本选型：**与内网项目 / sdk/web 的 sherpa-onnx-node 保持同版本（1.12.1）**，
#      进程内 dll 按模块名去重后是同一版本，任何加载顺序都无冲突。
#   B. 已安装 sherpa-onnx（make install）：-Dsherpa-onnx_DIR=<prefix>/lib/cmake/sherpa-onnx
set(AUDIOTSE_SHERPA_ROOT "" CACHE PATH "sherpa-onnx 官方预编译包根目录（含 include/ lib/）")

set(AUDIOTSE_SHERPA_TARGET)
set(AUDIOTSE_SHERPA_DLLS)

if(AUDIOTSE_SHERPA_ROOT STREQUAL "")
    # 自动探测：取字典序最大的 sherpa-onnx-* 目录（新版包字典序更大）
    file(GLOB _sherpa_candidates "${AUDIOTSE_NAPI_ROOT}/third_party/sherpa-onnx-*")
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
    # 预编译包只发布 cxx-api（C API 的 C++ 封装），内部 csrc 库未随包分发。
    # c-api 目标必须显式导入：embedder.cpp 直接调 C API（1.12.1 的 cxx-api 还没有
    # SpeakerEmbeddingExtractor 包装），这些符号在 sherpa-onnx-c-api.dll 里。
    # onnxruntime.lib 需从 dll 生成（1.12.1 包不带导入库，build.ps1 之前由 dumpbin+lib 生成）
    add_library(onnxruntime SHARED IMPORTED)
    set_target_properties(onnxruntime PROPERTIES
        IMPORTED_LOCATION "${AUDIOTSE_SHERPA_ROOT}/lib/onnxruntime.dll"
        IMPORTED_IMPLIB "${AUDIOTSE_SHERPA_ROOT}/lib/onnxruntime.lib")
    add_library(sherpa-onnx-c-api SHARED IMPORTED)
    set_target_properties(sherpa-onnx-c-api PROPERTIES
        IMPORTED_LOCATION "${AUDIOTSE_SHERPA_ROOT}/lib/sherpa-onnx-c-api.dll"
        IMPORTED_IMPLIB "${AUDIOTSE_SHERPA_ROOT}/lib/sherpa-onnx-c-api.lib")
    add_library(sherpa-onnx-cxx-api SHARED IMPORTED)
    set_target_properties(sherpa-onnx-cxx-api PROPERTIES
        IMPORTED_LOCATION "${AUDIOTSE_SHERPA_ROOT}/lib/sherpa-onnx-cxx-api.dll"
        IMPORTED_IMPLIB "${AUDIOTSE_SHERPA_ROOT}/lib/sherpa-onnx-cxx-api.lib"
        INTERFACE_INCLUDE_DIRECTORIES "${AUDIOTSE_SHERPA_ROOT}/include")
    target_link_libraries(sherpa-onnx-cxx-api INTERFACE sherpa-onnx-c-api onnxruntime)
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
