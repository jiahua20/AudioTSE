// Windows 延迟加载钩子（node-gyp 同款机制）：addon 以 /DELAYLOAD:NODE.EXE 链接，
// napi 符号首次调用时才从 "NODE.EXE" 解析——在 Electron 里宿主是 electron.exe，
// 不重定向的话会 LoadLibrary 到 PATH 里的系统 node.exe（版本错配，挂死/崩溃）。
// 本钩子把 NODE.EXE 的解析改到当前宿主进程模块，Node/Electron 通吃。
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <delayimp.h>
#include <string.h>

static FARPROC WINAPI load_exe_hook(unsigned int event, DelayLoadInfo *info) {
    if (event == dliNotePreLoadLibrary) {
        if (_stricmp(info->szDll, "node.exe") == 0) {
            // 当前进程可执行模块（node.exe 或 electron.exe）
            return reinterpret_cast<FARPROC>(GetModuleHandleW(nullptr));
        }
    }
    return nullptr;
}

extern "C" const PfnDliHook __pfnDliNotifyHook2 = load_exe_hook;
