@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo TI-LEX CODEX n'est pas installe.
  echo Lance d'abord install_windows.ps1 dans PowerShell.
  pause
  exit /b 1
)
if not exist ".tilex" mkdir ".tilex"
".venv\Scripts\python.exe" codex_gui.py 2>> ".tilex\gui_console.log"
if errorlevel 1 (
  echo.
  echo TI-LEX CODEX a rencontre une erreur.
  echo Regarde .tilex\gui_console.log
  pause
)
