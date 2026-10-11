@echo off
REM Build the single-file Windows GUI executable.
REM Output: dist\VideoToolbox.exe-renamed-to Chinese name by the spec file.
cd /d "%~dp0"
python -m PyInstaller --noconfirm --clean VideoToolbox.spec
if errorlevel 1 (
    echo.
    echo BUILD FAILED. Check the messages above.
    pause
    exit /b 1
)
echo.
echo BUILD OK  -^> dist folder
pause
