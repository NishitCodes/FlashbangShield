@echo off
setlocal
echo Removing Flashbang Shield from Windows Startup...
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "FlashbangShield" /f >nul 2>&1
echo [SUCCESS] Flashbang Shield removed from Windows Startup.
pause
