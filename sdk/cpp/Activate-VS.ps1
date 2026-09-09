<#
.SYNOPSIS
    激活 Visual Studio 开发环境，使 cmake、ninja、cl、msbuild 等工具在当前 PowerShell 会话中可用。
.DESCRIPTION
    使用 vswhere 自动查找 Visual Studio 安装路径，调用 vcvars64.bat（x64 原生编译环境）
    并将其设置的环境变量导入当前 PowerShell 会话，无需手动运行 vcvarsall.bat。

    幂等性: 若当前会话已激活 (检测 VSCMD_VER), 则直接跳过, 避免重复追加 PATH
    导致超过 cmd.exe 8KB 命令行上限 (报错 "输入行太长").
.EXAMPLE
    . .\Activate-VS.ps1
    PS> cmake --version
    PS> cl
#>

# Step 0: 幂等性检测 —— 已经激活过就跳过
# vcvars64.bat 会设置 VSCMD_VER / VCToolsInstallDir / VSINSTALLDIR 等。
# 若在同一 PowerShell 会话里反复调用本脚本, 每次 vcvars 都会往 PATH 追加
# 约 1.5KB 的 VS 目录, 累积到 ~8KB 后 cmd 内部的 `set PATH=%PATH%;...` 会
# 抛 "输入行太长"。
if ($env:VSCMD_VER) {
    Write-Host "VS environment already activated (VSCMD_VER=$($env:VSCMD_VER)), skipping." -ForegroundColor DarkGray
    # 仍然简单验证一下 cl 在 PATH 里, 防止用户误删
    $cl = Get-Command cl -ErrorAction SilentlyContinue
    if ($cl) {
        Write-Host "  [OK] cl -> $($cl.Source)" -ForegroundColor DarkGray
    } else {
        Write-Warning "VSCMD_VER set but 'cl' not in PATH; environment may be corrupted. Open a new shell if build fails."
    }
    return
}

# Step 1: locate vswhere（标准安装器位置；本机 VS 装在 D:\App 也能找到）
$vswhere = "C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) {
    Write-Warning "vswhere.exe not found. Assuming cmake/cl already in PATH."
    return
}

# Step 2: get latest VS install path
$vsPath = & $vswhere -latest -property installationPath
if (-not $vsPath) {
    Write-Warning "No Visual Studio installation found via vswhere. Assuming cmake/cl already in PATH."
    return
}

Write-Host "Found Visual Studio: $vsPath" -ForegroundColor Green

# Step 3: locate vcvars64.bat
$vcvars = Join-Path $vsPath "VC\Auxiliary\Build\vcvars64.bat"
if (-not (Test-Path $vcvars)) {
    Write-Warning "vcvars64.bat not found at: $vcvars. Assuming cmake/cl already in PATH."
    return
}

# Step 4: run vcvars64.bat via temp batch file and dump resulting env vars
$activated = $false
$tempBat = [IO.Path]::GetTempFileName() + ".bat"
try {
    "@echo off`ncall `"$vcvars`" >nul`nset" | Set-Content $tempBat -Encoding ASCII
    $envOutput = cmd /c $tempBat 2>&1
    if (-not $envOutput) {
        # Fallback: try without redirecting vcvars output
        "@echo off`ncall `"$vcvars`"`nset" | Set-Content $tempBat -Encoding ASCII
        $envOutput = cmd /c $tempBat 2>&1 | Where-Object { $_ -match '=' -and $_ -notmatch '^\[' }
    }
    if ($envOutput -and ($envOutput | Select-Object -First 1) -is [string]) {
        # Step 5: import environment vars into current PowerShell session
        $envOutput | ForEach-Object {
            $idx = $_.IndexOf('=')
            if ($idx -gt 0) {
                $name = $_.Substring(0, $idx)
                $value = $_.Substring($idx + 1)
                Set-Item -Path "Env:$name" -Value $value -ErrorAction SilentlyContinue
            }
        }
        $activated = $true
    }
} finally {
    Remove-Item $tempBat -Force -ErrorAction SilentlyContinue
}

if (-not $activated) {
    Write-Warning "vcvars64.bat did not produce expected output. Assuming cmake/cl already in PATH."
    return
}

# Step 6: verify key tools
$tools = @('cmake', 'ninja', 'cl', 'msbuild')
Write-Host "`nEnvironment activated. Available tools:" -ForegroundColor Green
foreach ($tool in $tools) {
    $exe = Get-Command $tool -ErrorAction SilentlyContinue
    if ($exe) {
        Write-Host "  [OK] $tool -> $($exe.Source)" -ForegroundColor Green
    } else {
        Write-Host "  [--] $tool not found" -ForegroundColor Gray
    }
}
