@echo off
cd /d "%~dp0"
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
.venv\Scripts\python.exe -m PyInstaller --clean WindowsPet.spec
if errorlevel 1 exit /b %errorlevel%
