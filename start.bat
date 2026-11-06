@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto install
where python >nul 2>&1
if errorlevel 1 (
    py -3 -m venv .venv
) else (
    python -m venv .venv
)
if errorlevel 1 goto failed
:install
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto failed
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" goto run
if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" goto run
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto failed
:run
".venv\Scripts\python.exe" launch.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo Startup failed. See the error above.
pause
exit /b 1
