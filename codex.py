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
from rich.theme import Theme
from rich.highlighter import RegexHighlighter
from prompt_toolkit import PromptSession

from config import FONTS, load_config, save_config

TI_LEX_THEME = Theme({
    "tilex.command": "bold bright_cyan",
    "tilex.action": "bold deep_sky_blue1",
    "tilex.error": "bold bright_red",
    "tilex.danger": "bold red3",
    "tilex.warning": "bold dark_orange",
    "tilex.function": "bold hot_pink",
    "tilex.keyword": "bold medium_purple1",
    "tilex.module": "purple",
    "tilex.number": "bold bright_yellow",
    "tilex.info": "bold bright_green",
    "tilex.path": "cyan",
    "tilex.string": "green_yellow",
    "tilex.comment": "grey62 italic",
    "tilex.success": "bold spring_green2",
    "tilex.label": "bold dodger_blue2",
    "tilex.value": "bright_white",
})

class TILexInfoHighlighter(RegexHighlighter):
    base_style = "tilex."
    highlights = [
        r"(?P<info>\\b(?:\\d{1,3}\\.){3}\\d{1,3}\\b)",
        r"(?P<info>\\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\\b)",
        r"(?P<info>\\b(?:ID|UUID|PID)\\s*[:=#]?\\s*[A-Za-z0-9_-]+\\b)",
    ]

info_highlighter = TILexInfoHighlighter()
console = Console(theme=TI_LEX_THEME, highlighter=info_highlighter)
config = load_config()
session = PromptSession()
active_project = None

BANNER = """TI-LEX CODEX
LOCAL AI • CODING • GAMER DARK"""

HELP = """[tilex.command]/chat[/]              Discuter librement avec l'IA
[tilex.command]/retour[/]            Revenir au projet depuis le chat
[tilex.command]/nouveau[/]           Créer et ouvrir un nouveau projet
[tilex.command]/projets[/]           Choisir un autre projet
[tilex.command]/ouvrir FICHIER[/]    Afficher le code en couleurs
[tilex.command]/explique FICHIER[/]  Expliquer un fichier
[tilex.command]/corrige FICHIER[/]   Analyser et proposer une correction
[tilex.command]/run FICHIER[/]       Exécuter un fichier Python après confirmation
[tilex.command]/police[/]            Choisir une police
[tilex.command]/aide[/]              Afficher cette aide
[tilex.command]/quitter[/]           Fermer TI-LEX CODEX

[tilex.info]Dans un projet, écris directement ce que tu veux coder :[/]
[tilex.value]ex. « crée une calculatrice Python » ou « explique mon main.py »[/]"""

def fluid(text, delay=None):
    delay = config.get("stream_delay", 0.008) if delay is None else delay
    for chunk in text.split(" "):
        console.print(chunk + " ", end="")
        time.sleep(delay)
    console.print()

def choose_font():
    global config
    table = Table(title="TI-LEX CODEX • 20 POLICES", border_style="medium_purple1")
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

def project_path(name):
    p = Path(name).expanduser()
    if not p.is_absolute() and active_project:
        p = active_project / p
    return p

def show_file(name):
    p = project_path(name)
    if not p.is_file():
        console.print("[tilex.error]Fichier introuvable.[/]")
        return
    lexer = "python" if p.suffix == ".py" else "text"
    console.print(Syntax(p.read_text(errors="replace"), lexer, theme="dracula", line_numbers=True, word_wrap=True))

def ai_file(name, action):
    p = project_path(name)
    if not p.is_file():
        console.print("[red]Fichier introuvable.[/]")
        return
    code = p.read_text(errors="replace")
    ollama(f"Tu es TI-LEX CODEX. {action} ce code clairement, en français. Ne modifie aucun fichier sans confirmation.\n\n{code}")

def run_file(name):
    p = project_path(name)
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

def projects_root():
    return Path.home() / "TI-LEX-Projets"

def list_projects():
    root = projects_root()
    root.mkdir(parents=True, exist_ok=True)
    projects = sorted([p for p in root.iterdir() if p.is_dir()])
    if not projects:
        console.print("[tilex.warning]Aucun projet local.[/]")
        return
    table = Table(title="TI-LEX CODEX • PROJETS", border_style="medium_purple1")
    table.add_column("#", style="bright_yellow")
    table.add_column("Projet", style="bright_cyan")
    table.add_column("Chemin", style="bright_green")
    for i, p in enumerate(projects, 1):
        table.add_row(str(i), p.name, str(p))
    console.print(table)

def select_project(name):
    global active_project
    p = projects_root() / name.strip()
    if not p.is_dir():
        console.print("[tilex.error]Projet introuvable. Utilise -projets pour voir la liste.[/]")
        return
    active_project = p
    console.print(f"[tilex.success]✓ Projet actif : {p.name}[/]")
    console.print(f"[tilex.info]DOSSIER : {p}[/]")

def create_project(name):
    name = name.strip()
    if not name:
        console.print("[yellow]Utilisation : -projet NOM[/]")
        return
    safe_name = "".join(ch for ch in name if ch.isalnum() or ch in "-_ ").strip().replace(" ", "-")
    if not safe_name:
        console.print("[red]Nom de projet invalide.[/]")
        return
    global active_project
    root = projects_root() / safe_name
    if root.exists():
        console.print(f"[yellow]Le projet existe déjà : {root}[/]")
        return
    root.mkdir(parents=True)
    (root / "src").mkdir()
    (root / "main.py").write_text('print("Bonjour TI-LEX")\n', encoding="utf-8")
    (root / "README.md").write_text(f"# {safe_name}\n\nProjet local créé avec TI-LEX CODEX.\n", encoding="utf-8")
    (root / "requirements.txt").write_text("", encoding="utf-8")
    console.print(f"[tilex.success]✓ Nouveau projet local créé : {root}[/]")
    console.print("[cyan]Fichiers : main.py, README.md, requirements.txt, src/[/]")
    active_project = root
    console.print(f"[tilex.info]PROJET ACTIF : {root.name}[/]")
    console.print("[dim]Aucun fichier n'a été envoyé sur Internet ou GitHub.[/]")

def startup_menu():
    global active_project
    while active_project is None:
        console.print(Panel(
            "[tilex.action]1[/]  [tilex.value]NOUVEAU PROJET[/]\n"
            "[tilex.action]2[/]  [tilex.value]CONTINUER UN PROJET[/]",
            title="TI-LEX CODEX • DÉMARRAGE",
            border_style="medium_purple1"
        ))
        choice = session.prompt("CHOIX › ").strip()
        if choice == "1":
            name = session.prompt("Nom du projet › ").strip()
            create_project(name)
        elif choice == "2":
            root = projects_root()
            root.mkdir(parents=True, exist_ok=True)
            projects = sorted([p for p in root.iterdir() if p.is_dir()])
            if not projects:
                console.print("[tilex.warning]Aucun projet. Crée ton premier projet.[/]")
                continue
            table = Table(title="CHOISIR UN PROJET", border_style="medium_purple1")
            table.add_column("#", style="bright_yellow")
            table.add_column("Projet", style="bright_cyan")
            for i, p in enumerate(projects, 1):
                table.add_row(str(i), p.name)
            console.print(table)
            try:
                n = int(session.prompt("Projet › ").strip())
                if 1 <= n <= len(projects):
                    active_project = projects[n - 1]
                    console.print(f"[tilex.success]✓ Projet chargé : {active_project.name}[/]")
                else:
                    console.print("[tilex.error]Choix invalide.[/]")
            except ValueError:
                console.print("[tilex.error]Entre le numéro du projet.[/]")
        else:
            console.print("[tilex.warning]Choisis 1 ou 2.[/]")

def project_context():
    if not active_project:
        return ""
    files = []
    for p in active_project.rglob("*"):
        if p.is_file() and ".git" not in p.parts:
            try:
                files.append(str(p.relative_to(active_project)))
            except ValueError:
                pass
        if len(files) >= 60:
            break
    return (
        f"Tu es TI-LEX CODEX, un assistant de programmation local. "
        f"Le projet actif est {active_project.name}. "
        f"Chemin: {active_project}. "
        f"Fichiers: {', '.join(files) if files else '(vide)'}. "
        "Réponds en français, de façon pratique et orientée code. "
        "Ne prétends jamais avoir modifié un fichier si aucune opération locale ne l'a réellement modifié. "
    )

def chat_loop(help_mode=False):
    if help_mode:
        console.clear()
        console.print(Panel(HELP, title="TI-LEX CODEX • AIDE IA", border_style="bright_green"))
        fluid("Pose-moi une question sur Python, ton projet ou les commandes. Écris -retour pour revenir.")
    while True:
        q = session.prompt("TOI › ").strip()
        if q in ("/retour", "-retour"):
            return
        if q:
            ollama(("Tu aides l'utilisateur à comprendre TI-LEX CODEX et la programmation. " if help_mode else "Tu es TI-LEX CODEX, assistant de programmation. ") + q)

def main():
    console.clear()
    console.print(Panel(Text(BANNER, style="bold bright_green", justify="center"), border_style="medium_purple1"))
    if not (Path.home() / ".ti_lex_codex" / "config.json").exists():
        choose_font()

    startup_menu()
    console.print(f"[tilex.success]✓ MODE CODEX • PROJET : {active_project.name}[/]")
    console.print("[tilex.info]Écris directement ce que tu veux coder. /aide pour les commandes, /chat pour discuter.[/]")

    while True:
        cmd = session.prompt(f"TI-LEX CODEX [{active_project.name}] › ").strip()
        if not cmd:
            continue

        if cmd in ("/quitter", "-quitter"):
            break
        elif cmd in ("/aide", "-aide"):
            console.print(Panel(HELP, title="TI-LEX CODEX • AIDE", border_style="medium_purple1"))
        elif cmd in ("/chat", "-chat"):
            console.print("[tilex.info]MODE CHAT • /retour pour revenir au projet[/]")
            chat_loop(False)
            console.print(f"[tilex.success]Retour au projet : {active_project.name}[/]")
        elif cmd in ("/nouveau",):
            name = session.prompt("Nom du projet › ").strip()
            create_project(name)
        elif cmd in ("/projets", "-projets"):
            old = active_project
            active_project = None
            startup_menu()
            if active_project is None:
                active_project = old
        elif cmd in ("/police", "-police"):
            choose_font()
        elif cmd.startswith("/ouvrir ") or cmd.startswith("-ouvrir "):
            show_file(cmd.split(" ", 1)[1].strip())
        elif cmd.startswith("/explique ") or cmd.startswith("-explique "):
            ai_file(cmd.split(" ", 1)[1].strip(), "Explique")
        elif cmd.startswith("/corrige ") or cmd.startswith("-corrige "):
            ai_file(cmd.split(" ", 1)[1].strip(), "Analyse les erreurs et propose une version corrigée de")
        elif cmd.startswith("/run ") or cmd.startswith("-run "):
            run_file(cmd.split(" ", 1)[1].strip())
        elif cmd.startswith("-projet "):
            create_project(cmd[8:].strip())
        elif cmd.startswith("-selection "):
            select_project(cmd[11:].strip())
        else:
            ollama(project_context() + "\nDemande de l'utilisateur dans le projet actif : " + cmd)

if __name__ == "__main__":
    main()
