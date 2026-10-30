@echo off
rem stop.bat - ask the running screen (server) to exit.
setlocal
cd /d "%~dp0"
set PY=python
if exist "config\python_path.txt" for /f "usebackq delims=" %%p in ("config\python_path.txt") do set PY=%%p
"%PY%" start_app.py --stop
echo.
echo exit code: %ERRORLEVEL%
pause
