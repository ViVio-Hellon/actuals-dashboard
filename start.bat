@echo off
rem start.bat - diagnostic launcher. Same screen as Start.vbs, but this console stays open for logs.
rem   start.bat                                   open the browser screen
rem   start.bat --import-workbook old.xlsm        import DATA / overrides from the old workbook
setlocal
cd /d "%~dp0"
set PY=python
if exist "config\python_path.txt" for /f "usebackq delims=" %%p in ("config\python_path.txt") do set PY=%%p
"%PY%" start_app.py %*
echo.
echo exit code: %ERRORLEVEL%
pause
