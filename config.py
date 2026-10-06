from pathlib import Path
import json

APP_DIR = Path.home() / ".ti_lex_codex"
CONFIG_FILE = APP_DIR / "config.json"

FONTS = [
    "JetBrains Mono", "Fira Code", "Hack", "Source Code Pro", "Ubuntu Mono",
    "DejaVu Sans Mono", "Liberation Mono", "Noto Sans Mono", "Cascadia Code",
    "Cascadia Mono", "IBM Plex Mono", "Inconsolata", "Roboto Mono", "Space Mono",
    "Anonymous Pro", "Mononoki", "Victor Mono", "Iosevka", "Terminus", "MesloLGS NF",
]

DEFAULTS = {"font": "JetBrains Mono", "model": "qwen2.5:7b", "stream_delay": 0.008}

def load_config():
    APP_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        return DEFAULTS.copy()
    try:
        return {**DEFAULTS, **json.loads(CONFIG_FILE.read_text())}
    except Exception:
        return DEFAULTS.copy()

def save_config(config):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))
