#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "TI-LEX CODEX n'est pas installe."
  echo "Lance d'abord : ./install.sh"
  exit 1
fi

exec .venv/bin/python codex_gui.py
