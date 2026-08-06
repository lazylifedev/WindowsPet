@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (echo .venv not found. Run: py -3.12 -m venv .venv & exit /b 1)
.venv\Scripts\python.exe -m windows_pet.main
