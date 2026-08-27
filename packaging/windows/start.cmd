@echo off
cd /d "%~dp0"
SigmaMaia.exe --config "%~dp0config\app.env"
if errorlevel 1 pause
