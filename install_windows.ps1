$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "=== TI-LEX CODEX - Installation Windows ===" -ForegroundColor Green

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Python est introuvable. Installe Python 3 puis relance ce script." -ForegroundColor Red
    exit 1
}

python -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\pip.exe" install -r requirements.txt

if (Get-Command ollama -ErrorAction SilentlyContinue) {
    Write-Host "Ollama detecte." -ForegroundColor Green
    Write-Host "Modele recommande : ollama pull qwen2.5-coder:7b" -ForegroundColor Cyan
} else {
    Write-Host "Ollama n'est pas encore installe. TI-LEX CODEX fonctionnera apres son installation." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Installation terminee." -ForegroundColor Green
Write-Host "Lance TI-LEX CODEX avec : .\start_codex.bat" -ForegroundColor Cyan
