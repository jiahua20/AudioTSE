cmake_minimum_required(VERSION 3.16)
project(audiotse-gate LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# sherpa-onnx 获取方式二选一（默认走 A）：
#   A. 已安装 sherpa-onnx（make install 到系统或自定义前缀）
#      若安装在非默认前缀，配置时传 -Dsherpa-onnx_DIR=<prefix>/lib/cmake/sherpa-onnx
#   B. -DAUDIOTSE_FETCH_SHERPA=ON：源码拉取并连同构建（首次很慢，需网络）
option(AUDIOTSE_FETCH_SHERPA "FetchContent 拉取 sherpa-onnx 源码一起构建" OFF)

if(AUDIOTSE_FETCH_SHERPA)
    include(FetchContent)
    set(SHERPA_ONNX_ENABLE_PYTHON OFF CACHE BOOL "" FORCE)
    set(SHERPA_ONNX_ENABLE_TESTS OFF CACHE BOOL "" FORCE)
    set(BUILD_SHARED_LIBS ON CACHE BOOL "" FORCE)
    FetchContent_Declare(
        sherpa-onnx
        GIT_REPOSITORY https://github.com/k2-fsa/sherpa-onnx.git
        GIT_TAG v1.13.7
        GIT_SHALLOW TRUE
    )
    FetchContent_MakeAvailable(sherpa-onnx)
else()
    find_package(sherpa-onnx REQUIRED)
endif()

# SDK 静态库
add_library(audiotse_gate STATIC src/speaker_gate.cpp)
target_include_directories(audiotse_gate PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(audiotse_gate PUBLIC sherpa-onnx-core)

# CLI 示例（在仓库根目录运行，模型路径见源码默认值）
add_executable(gate_demo examples/gate_demo.cc)
target_link_libraries(gate_demo PRIVATE audiotse_gate)
