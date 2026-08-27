@echo off
cd /d "%~dp0"
SigmaMaia.exe --config "%~dp0config\app.env" --check-config
pause
