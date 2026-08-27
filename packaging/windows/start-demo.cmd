@echo off
cd /d "%~dp0"
set "MAIA_ENABLE_DEMO=1"
set "MAIA_HOST=0.0.0.0"
SigmaMaia.exe --config "%~dp0config\app.env"
if errorlevel 1 pause
