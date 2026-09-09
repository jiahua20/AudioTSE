# sherpa-onnx 预编译包的探测与导入，供 sdk/cpp 与 sdk/cpp-napi 两个构建根共享。
# include 本文件后可用两个变量：
#   AUDIOTSE_SHERPA_TARGET  应链接的 CMake target 名
#   AUDIOTSE_SHERPA_DLLS    随产物分发的运行时 dll 列表（POST_BUILD 拷贝用）
if(AUDIOTSE_SHERPA_INCLUDED)
    return()
endif()
set(AUDIOTSE_SHERPA_INCLUDED ON)

# 本文件位于 sdk/cpp/cmake/ 下，SDK 根目录（third_party/ 在这里）取上一级
get_filename_component(AUDIOTSE_CPP_ROOT "${CMAKE_CURRENT_LIST_DIR}/.." ABSOLUTE)

# sherpa-onnx 依赖，二选一（默认 A）：
#   A. 官方 win-x64 预编译包（含 include/ lib/ bin/），目录名保留官方发布全名
#      （如 sherpa-onnx-v1.13.7-win-x64-shared-MD-Release，版本一目了然）：
#      - AUDIOTSE_SHERPA_ROOT 不传时自动探测 sdk/cpp/third_party/sherpa-onnx-*
#        （build.ps1 会下载解压官方包到这，不改名）
#      - 手动传 -DAUDIOTSE_SHERPA_ROOT=<包目录>
#   B. 已安装 sherpa-onnx（make install）：-Dsherpa-onnx_DIR=<prefix>/lib/cmake/sherpa-onnx
set(AUDIOTSE_SHERPA_ROOT "" CACHE PATH "sherpa-onnx 官方预编译包根目录（含 include/ lib/ bin/）")

set(AUDIOTSE_SHERPA_TARGET)
set(AUDIOTSE_SHERPA_DLLS)

if(AUDIOTSE_SHERPA_ROOT STREQUAL "")
    # 自动探测：取字典序最大的 sherpa-onnx-* 目录（新版包字典序更大）
    file(GLOB _sherpa_candidates "${AUDIOTSE_CPP_ROOT}/third_party/sherpa-onnx-*")
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
