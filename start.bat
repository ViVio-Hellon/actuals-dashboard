@echo off
rem start.bat - diagnostic launcher. Same screen as Start.vbs, but this console stays open for logs.
rem   start.bat                                   open the browser screen
rem   start.bat --import-workbook old.xlsm        import DATA / overrides from the old workbook
setlocal
cd /d "%~dp0"
set PY=python
if exist "config\python_path.txt" for /f "usebackq delims=" %%p in ("config\python_path.txt") do set PY=%%p
rem pythonw.exe has no console; use python.exe next to it so the log is shown
if /i "%PY:~-11%"=="pythonw.exe" set PY=%PY:~0,-11%python.exe
"%PY%" start_app.py %*
echo.
echo exit code: %ERRORLEVEL%
pause
