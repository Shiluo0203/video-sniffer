@echo off
REM Build the floating-window sniffer as a single-file Windows GUI executable.
REM Output: dist\FloatSniffer.exe-renamed-to Chinese name by the spec file.
cd /d "%~dp0"
python -m PyInstaller --noconfirm --clean FloatSniffer.spec
if errorlevel 1 (
    echo.
    echo BUILD FAILED. Check the messages above.
    pause
    exit /b 1
)
echo.
echo BUILD OK  -^> dist folder
pause
