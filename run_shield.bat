@echo off
setlocal
cd /d "%~dp0"
start "" pythonw.exe -m flashbang_shield.main %*
echo Flashbang Shield launched in background.
