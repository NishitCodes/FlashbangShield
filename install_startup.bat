@echo off
setlocal
cd /d "%~dp0"
echo Registering Flashbang Shield to Windows Startup...

for /f "delims=" %%I in ('where pythonw.exe 2^>nul') do set "PYW=%%I"
if not defined PYW set "PYW=C:\Python314\pythonw.exe"

reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "FlashbangShield" /t REG_SZ /d "\"%PYW%\" \"%~dp0flashbang_shield\main.py\" --minimized" /f >nul
if %errorlevel% equ 0 (
    echo [SUCCESS] Flashbang Shield is now configured to start automatically on Windows boot.
) else (
    echo [ERROR] Failed to set registry startup entry.
)
pause
