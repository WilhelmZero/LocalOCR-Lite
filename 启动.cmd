@echo off
cd /d "%~dp0"
if exist "runtime\pythonw.exe" (
  start "" "runtime\pythonw.exe" "launch.pyw"
) else if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" "launch.pyw"
) else (
  echo Python runtime missing. Run install.ps1 first.
  pause
)
