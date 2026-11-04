@echo off
cd /d "%~dp0"
python run.py
if errorlevel 1 (
  echo.
  echo Start failed. Check Python: python --version
  echo Or run manually:  python run.py
  pause
)
