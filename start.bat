@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto install
set "PYTHON_CMD="
call :try_python python
if defined PYTHON_CMD goto create_venv
call :try_python py -3
if defined PYTHON_CMD goto create_venv
for %%P in ("%UserProfile%\miniconda3\python.exe" "%UserProfile%\anaconda3\python.exe" "%LocalAppData%\miniconda3\python.exe" "%LocalAppData%\anaconda3\python.exe" "%ProgramData%\miniconda3\python.exe" "%ProgramData%\anaconda3\python.exe") do (
    if not defined PYTHON_CMD if exist "%%~P" call :try_python "%%~P"
)
for /d %%D in ("%LocalAppData%\Programs\Python\Python*" "%ProgramFiles%\Python*") do (
    if not defined PYTHON_CMD if exist "%%~D\python.exe" call :try_python "%%~D\python.exe"
)
if defined PYTHON_CMD goto create_venv
echo No working Python 3.11 or newer was found.
echo Install Python 3.11+ or add your Python installation to PATH, then retry.
goto failed
:create_venv
echo Creating virtual environment with %PYTHON_CMD%
%PYTHON_CMD% -m venv .venv
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

:try_python
%* -c "import sys, venv; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 exit /b 0
set "PYTHON_CMD=%*"
exit /b 0
