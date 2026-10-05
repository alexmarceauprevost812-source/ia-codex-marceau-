#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
echo
echo "TI-LEX CODEX installé."
echo "Lancement : source .venv/bin/activate && python codex.py"
echo "IA locale : installe Ollama, puis lance : ollama pull qwen2.5-coder:7b"
