@echo off
cd /d "%~dp0"
py -3.12 tools\install_minimal.py
if errorlevel 1 (
  echo Installation failed. Install Python 3.12 and retry.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "launch.pyw"
