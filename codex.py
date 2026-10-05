#!/usr/bin/env python3
from pathlib import Path
import subprocess
import sys
import time
import requests

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from prompt_toolkit import PromptSession

from config import FONTS, load_config, save_config

console = Console()
config = load_config()
session = PromptSession()

BANNER = """TI-LEX CODEX
LOCAL AI • CODING • GAMER DARK"""

HELP = """[bold bright_green]-chat[/]              Chat libre avec l'IA
[bold cyan]-ouvrir fichier.py[/]  Afficher un fichier avec coloration
[bold magenta]-explique fichier[/] Expliquer le code avec l'IA
[bold yellow]-corrige fichier[/]  Proposer une correction
[bold red]-run fichier.py[/]      Exécuter après confirmation
[bold bright_cyan]-police[/]            Choisir parmi 20 polices
[bold white]-aide[/]              Ouvrir cette page/chat d'aide
[bold white]-retour[/]            Quitter le mode aide/chat
[bold white]-quitter[/]           Fermer TI-LEX CODEX"""

def fluid(text, delay=None):
    delay = config.get("stream_delay", 0.008) if delay is None else delay
    for chunk in text.split(" "):
        console.print(chunk + " ", end="")
        time.sleep(delay)
    console.print()

def choose_font():
    global config
    table = Table(title="TI-LEX CODEX • 20 POLICES", border_style="bright_green")
    table.add_column("#", style="bright_cyan", justify="right")
    table.add_column("Police", style="white")
    for i, font in enumerate(FONTS, 1):
        table.add_row(f"{i:02}", font)
    console.print(table)
    try:
        n = int(console.input("[bright_green]Choix › [/]"))
        if 1 <= n <= len(FONTS):
            config["font"] = FONTS[n - 1]
            save_config(config)
            console.print(f"[bright_green]✓ Police choisie : {config['font']}[/]")
            console.print("[dim]Note : le terminal doit avoir cette police installée et peut nécessiter de la sélectionner dans ses préférences.[/]")
    except ValueError:
        console.print("[red]Choix invalide.[/]")

def ollama(prompt):
    model = config["model"]
    try:
        with requests.post(
            "http://127.0.0.1:11434/api/generate",
            json={"model": model, "prompt": prompt, "stream": True},
            stream=True, timeout=180
        ) as r:
            r.raise_for_status()
            import json
            console.print("[bright_green]IA › [/]", end="")
            for line in r.iter_lines():
                if line:
                    token = json.loads(line).get("response", "")
                    console.print(token, end="", markup=False)
            console.print()
    except Exception as exc:
        console.print(f"[red]Ollama indisponible : {exc}[/]")
        console.print(f"[yellow]Vérifie Ollama puis : ollama pull {model}[/]")

def show_file(name):
    p = Path(name).expanduser()
    if not p.is_file():
        console.print("[red]Fichier introuvable.[/]")
        return
    lexer = "python" if p.suffix == ".py" else "text"
    console.print(Syntax(p.read_text(errors="replace"), lexer, theme="monokai", line_numbers=True, word_wrap=True))

def ai_file(name, action):
    p = Path(name).expanduser()
    if not p.is_file():
        console.print("[red]Fichier introuvable.[/]")
        return
    code = p.read_text(errors="replace")
    ollama(f"Tu es TI-LEX CODEX. {action} ce code clairement, en français. Ne modifie aucun fichier sans confirmation.\n\n{code}")

def run_file(name):
    p = Path(name).expanduser()
    if not p.is_file():
        console.print("[red]Fichier introuvable.[/]")
        return
    if console.input(f"[yellow]Exécuter {p}? (oui/non) › [/]").strip().lower() != "oui":
        console.print("[dim]Annulé.[/]")
        return
    if p.suffix == ".py":
        subprocess.run([sys.executable, str(p)], check=False)
    else:
        console.print("[red]V1 : exécution automatique limitée aux fichiers Python.[/]")

def chat_loop(help_mode=False):
    if help_mode:
        console.clear()
        console.print(Panel(HELP, title="TI-LEX CODEX • AIDE IA", border_style="bright_green"))
        fluid("Pose-moi une question sur Python, ton projet ou les commandes. Écris -retour pour revenir.")
    while True:
        q = session.prompt("TOI › ").strip()
        if q == "-retour":
            return
        if q:
            ollama(("Tu aides l'utilisateur à comprendre TI-LEX CODEX et la programmation. " if help_mode else "Tu es TI-LEX CODEX, assistant de programmation. ") + q)

def main():
    console.clear()
    console.print(Panel(Text(BANNER, style="bold bright_green", justify="center"), border_style="bright_green"))
    if not (Path.home() / ".ti_lex_codex" / "config.json").exists():
        choose_font()
    fluid(f"Bienvenue dans TI-LEX CODEX. Police configurée : {config['font']}. Écris -aide pour commencer.")
    while True:
        cmd = session.prompt("TI-LEX CODEX › ").strip()
        if not cmd:
            continue
        if cmd == "-quitter":
            break
        elif cmd == "-aide":
            chat_loop(True)
        elif cmd == "-chat":
            chat_loop(False)
        elif cmd == "-police":
            choose_font()
        elif cmd.startswith("-ouvrir "):
            show_file(cmd[8:].strip())
        elif cmd.startswith("-explique "):
            ai_file(cmd[10:].strip(), "Explique")
        elif cmd.startswith("-corrige "):
            ai_file(cmd[9:].strip(), "Analyse les erreurs et propose une version corrigée de")
        elif cmd.startswith("-run "):
            run_file(cmd[5:].strip())
        else:
            console.print("[yellow]Commande inconnue. Essaie -aide.[/]")

if __name__ == "__main__":
    main()
