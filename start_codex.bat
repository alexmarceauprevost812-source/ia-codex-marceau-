@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo TI-LEX CODEX n'est pas installe.
  echo Lance d'abord install_windows.ps1 dans PowerShell.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" codex.py
