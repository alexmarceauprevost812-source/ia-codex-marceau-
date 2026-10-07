@echo off
setlocal
cd /d "%~dp0"

echo.
echo ==========================================
echo   TI-LEX CODEX - MISE A JOUR COMPLETE
echo ==========================================
echo.

where git >nul 2>nul
if errorlevel 1 (
  echo [ERREUR] Git est introuvable.
  echo Installe Git puis relance ce fichier.
  pause
  exit /b 1
)

echo [1/5] Mise a jour du projet...
git pull --ff-only origin main
if errorlevel 1 (
  echo.
  echo [ERREUR] Git n'a pas pu faire la mise a jour automatiquement.
  echo Lance "git status" pour voir les fichiers locaux modifies.
  pause
  exit /b 1
)

echo.
echo [2/5] Verification de Python...
set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py"

if not defined PYTHON_CMD (
  where python >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
  echo [ERREUR] Python est introuvable.
  echo Installe Python 3 puis relance .\update_codex.bat
  pause
  exit /b 1
)

echo.
echo [3/5] Verification de l'environnement virtuel...
if not exist ".venv\Scripts\python.exe" (
  echo Creation de .venv...
  %PYTHON_CMD% -m venv .venv
  if errorlevel 1 (
    echo [ERREUR] Impossible de creer .venv.
    pause
    exit /b 1
  )
)

echo.
echo [4/5] Mise a jour des dependances...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
  echo [ERREUR] Echec de la mise a jour de pip.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo [ERREUR] Echec de l'installation des dependances.
  pause
  exit /b 1
)

echo.
echo [5/5] Verification rapide du code...
".venv\Scripts\python.exe" -m py_compile codex_engine.py codex_gui.py secret_store.py
if errorlevel 1 (
  echo [ERREUR] Une erreur Python a ete detectee.
  echo Le programme ne sera pas lance.
  pause
  exit /b 1
)

if not exist ".tilex" mkdir ".tilex"

echo.
echo ==========================================
echo   MISE A JOUR TERMINEE - LANCEMENT
echo ==========================================
echo.

call ".\start_codex_gui.bat"

endlocal
