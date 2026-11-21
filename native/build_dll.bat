@echo off
cd /d "%~dp0"
call "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars64.bat"

echo Compiling native shield_core.dll...
cl.exe /O2 /LD /std:c++20 /EHsc shield_core.cpp /Fe:..\flashbang_shield\shield_core.dll d3d11.lib dxgi.lib dxguid.lib d3dcompiler.lib user32.lib gdi32.lib shell32.lib

if %errorlevel% equ 0 (
    echo [SUCCESS] shield_core.dll compiled successfully into flashbang_shield\shield_core.dll!
    del shield_core.obj shield_core.exp shield_core.lib 2>nul
) else (
    echo [ERROR] Compilation failed with error code %errorlevel%.
)
