@echo off
rem stop.bat - stop the running screen (browser version or desktop version).
rem   1. asks the screen to exit (same as the [quit] button)
rem   2. if it does not answer or does not exit in 15 seconds, kills the process (taskkill /T /F) and removes the lock
setlocal
cd /d "%~dp0"
set PY=python
if exist "config\python_path.txt" for /f "usebackq delims=" %%p in ("config\python_path.txt") do set PY=%%p
rem pythonw.exe has no console; use python.exe next to it so the messages are shown
if /i "%PY:~-11%"=="pythonw.exe" set PY=%PY:~0,-11%python.exe
"%PY%" start_app.py --stop
echo.
set RC=%ERRORLEVEL%
echo exit code: %RC%
rem STOP_NOPAUSE=1 : called from another program (no pause)
if not "%STOP_NOPAUSE%"=="1" pause
exit /b %RC%
