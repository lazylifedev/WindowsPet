@echo off
set "ROOT=%~dp0"
cd /d "%ROOT%"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo .venv not found. Run: py -3.12 -m venv .venv
    exit /b 1
)

"%PYTHON%" -c "import windows_pet" >nul 2>&1
if errorlevel 1 (
    echo windows_pet is not installed. Installing editable package...
    "%PYTHON%" -m pip install -e "%ROOT%."
    if errorlevel 1 (
        echo Failed to install windows_pet. See the error above.
        exit /b 1
    )
    "%PYTHON%" -c "import windows_pet"
    if errorlevel 1 (
        echo windows_pet is still unavailable after installation.
        exit /b 1
    )
)

"%PYTHON%" -m windows_pet.main
