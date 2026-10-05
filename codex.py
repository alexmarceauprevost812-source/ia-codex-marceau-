#!/usr/bin/env python3
from pathlib import Path
import subprocess
import shutil
import sys
import time
import requests

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.style import Style
from pygments.style import Style as PygmentsStyle
from pygments.token import Text as PygmentsText, Whitespace, Comment, Keyword, Name, Number, Operator, String, Punctuation, Generic, Error
from rich.table import Table
from rich.columns import Columns
from rich.text import Text
from rich.theme import Theme
from rich.highlighter import RegexHighlighter
from prompt_toolkit import PromptSession
from textual.app import App, ComposeResult
from codex_engine import CodexEngine
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, ListView, ListItem, Label, TextArea, Input, Button, Static, RichLog
from textual.widgets.text_area import TextAreaTheme
from textual.binding import Binding

TI_LEX_TEXTAREA_THEME = TextAreaTheme(
    name="ti_lex_neon",
    base_style=Style(color="#EAF6FF", bgcolor="#000000"),
    syntax_styles={
        "comment": Style(color="#7D8590", italic=True),
        "keyword": Style(color="#D65CFF", bold=True),
        "keyword.function": Style(color="#D65CFF", bold=True),
        "keyword.operator": Style(color="#FF1493", bold=True),
        "string": Style(color="#39FF14"),
        "string.escape": Style(color="#00FF66", bold=True),
        "number": Style(color="#FFFF00"),
        "boolean": Style(color="#FFFF00", bold=True),
        "operator": Style(color="#FF1493"),
        "function": Style(color="#00BFFF", bold=True),
        "function.call": Style(color="#00E5FF"),
        "function.method": Style(color="#00BFFF"),
        "constructor": Style(color="#B026FF", bold=True),
        "type": Style(color="#00E5FF"),
        "class": Style(color="#B026FF", bold=True),
        "constant": Style(color="#00E5FF"),
        "variable": Style(color="#FFFFFF"),
        "variable.builtin": Style(color="#00E5FF"),
        "attribute": Style(color="#FF7A00"),
        "property": Style(color="#FF7A00"),
        "tag": Style(color="#D65CFF"),
        "label": Style(color="#00BFFF"),
        "punctuation": Style(color="#F0F6FC"),
    },
)

from config import FONTS, load_config, save_config

class TILexCodeStyle(PygmentsStyle):
    """Palette TI-LEX sombre inspirée des éditeurs modernes."""
    background_color = "#0d1117"
    highlight_color = "#263040"
    styles = {
        PygmentsText: "#EAF6FF",
        Whitespace: "#EAF6FF",
        Error: "bold #FF1744",
        Comment: "italic #7D8590",
        Keyword: "bold #D65CFF",
        Keyword.Type: "#00BFFF",
        Name: "#FFFFFF",
        Name.Builtin: "#00E5FF",
        Name.Function: "bold #00BFFF",
        Name.Class: "bold #B026FF",
        Name.Namespace: "#B026FF",
        Name.Decorator: "#FF7A00",
        Name.Exception: "#FF1744",
        Name.Constant: "#00E5FF",
        Name.Variable: "#FFFFFF",
        String: "#39FF14",
        String.Doc: "italic #00FF66",
        Number: "#FFFF00",
        Operator: "#FF1493",
        Punctuation: "#F0F6FC",
        Generic.Heading: "bold #00E5FF",
        Generic.Subheading: "bold #B026FF",
        Generic.Error: "#FF1744",
    }

CODE_STYLE = TILexCodeStyle

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
LOCAL AI • CODING • DEVELOPER TERMINAL"""

ASCII_TI_LEX = r""" ________  ____        __    _______  __
/_  __/ / / /         / /   / ____/ |/ /
 / / / /_/ /  ______ / /   / __/  |   /
/ / / __  /  /_____/ / /___/ /___ /   |
/_/ /_/ /_/          /_____/_____//_/|_|"""

ASCII_CODEX = r"""   ______ ____  ____  _______  __
  / ____// __ \/ __ \/ ____/ |/ /
 / /    / / / / / / / __/  |   /
/ /___ / /_/ / /_/ / /___ /   |
\____/ \____/_____/_____//_/|_|"""

def show_logo():
    console.print(ASCII_TI_LEX, style="bold dark_orange")
    console.print(ASCII_CODEX, style="bold #39FF14")
    console.print("[tilex.info]LOCAL AI • CODING • DEVELOPER TERMINAL[/]", justify="center")

HELP = """[tilex.action]PROJETS[/]
[tilex.command]/nouveau[/]             Créer un projet
[tilex.command]/projets[/]             Choisir/continuer un projet
[tilex.command]/supprimer[/]           Supprimer un projet (double confirmation)
[tilex.command]/etat[/]                Tableau de bord du projet actif
[tilex.command]/fichiers[/]            Lister les fichiers du projet

[tilex.action]CODE & FICHIERS[/]
[tilex.command]/ouvrir FICHIER[/]     Afficher le code en couleurs
[tilex.command]/chercher TEXTE[/]     Rechercher dans les fichiers du projet
[tilex.command]/explique FICHIER[/]   Expliquer un fichier avec l'IA
[tilex.command]/corrige FICHIER[/]    Analyser et proposer une correction
[tilex.command]/run FICHIER[/]        Exécuter un fichier Python après confirmation

[tilex.action]ASSISTANT[/]
[tilex.command]/chat[/]               Discussion avec l'IA
[tilex.command]/aide[/]               Aide et commandes
[tilex.command]/police[/]             Choisir la police
[tilex.command]/quitter[/]            Fermer TI-LEX CODEX

[tilex.info]Tu peux aussi écrire directement ce que tu veux coder dans le projet actif.[/]"""

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

def print_ai_response(text):
    """Affiche les réponses IA avec une coloration visible du code."""
    import re

    # 1) Blocs Markdown explicites : ```python ... ```
    pattern = re.compile(r"```([a-zA-Z0-9_+.-]*)\\s*\\n([\\s\\S]*?)```")
    pos = 0
    found = False
    for match in pattern.finditer(text):
        found = True
        prose = text[pos:match.start()].strip()
        if prose:
            fluid(prose, delay=0.025)

        language = (match.group(1) or "python").lower()
        aliases = {
            "py": "python", "python3": "python", "ps1": "powershell",
            "sh": "bash", "shell": "bash", "js": "javascript",
            "ts": "typescript", "html5": "html",
        }
        language = aliases.get(language, language)
        code = match.group(2).rstrip()
        rendered = Panel(
            Syntax(code, language, theme=CODE_STYLE, line_numbers=True,
                   word_wrap=False, background_color="default"),
            title=f"[tilex.action]CODE • {language.upper()}[/]",
            border_style="bright_cyan",
        )
        time.sleep(0.12)
        console.print(rendered)
        pos = match.end()

    if found:
        tail = text[pos:].strip()
        if tail:
            fluid(tail, delay=0.025)
        return

    # 2) Secours : si le modèle oublie les backticks mais la réponse ressemble
    # fortement à du Python, on la colore quand même.
    python_signals = (
        "def ", "class ", "import ", "from ", "print(", "if __name__",
        "for ", "while ", "return ", "try:", "except ", "= [", "= {"
    )
    score = sum(1 for signal in python_signals if signal in text)
    if score >= 2:
        rendered = Panel(
            Syntax(text.strip(), "python", theme=CODE_STYLE, line_numbers=True,
                   word_wrap=False, background_color="default"),
            title="[tilex.action]CODE • PYTHON[/]",
            border_style="bright_cyan",
        )
        time.sleep(0.12)
        console.print(rendered)
    else:
        fluid(text.strip(), delay=0.025)

def ollama(prompt):
    """Streaming réel : affiche la réponse pendant qu'Ollama la génère."""
    model = config["model"]
    try:
        with requests.post(
            "http://127.0.0.1:11434/api/generate",
            json={
                "model": model,
                "prompt": prompt + (
                    "\nQuand tu écris du code, mets TOUJOURS le code dans un bloc Markdown "
                    "avec son langage, par exemple ```python."
                ),
                "stream": True,
            },
            stream=True, timeout=180
        ) as r:
            r.raise_for_status()
            import json
            console.print("[tilex.success]IA ›[/]")
            buffer = ""
            in_code = False
            language = "python"
            code_buffer = ""

            for line in r.iter_lines():
                if not line:
                    continue
                token = json.loads(line).get("response", "")
                buffer += token

                # Détecte l'ouverture d'un bloc de code.
                if not in_code and "```" in buffer:
                    before, after = buffer.split("```", 1)
                    if before:
                        console.print(before, end="", style="tilex.value", highlight=True)
                    if "\n" in after:
                        lang, rest = after.split("\n", 1)
                        language = lang.strip() or "python"
                        language = {"py":"python","python3":"python","ps1":"powershell",
                                    "sh":"bash","shell":"bash","js":"javascript",
                                    "ts":"typescript"}.get(language.lower(), language.lower())
                        code_buffer = rest
                        buffer = ""
                        in_code = True
                    else:
                        buffer = "```" + after
                    continue

                if in_code:
                    code_buffer += buffer
                    buffer = ""
                    if "```" in code_buffer:
                        code, tail = code_buffer.split("```", 1)
                        console.print(Syntax(code.rstrip(), language, theme=CODE_STYLE,
                                             line_numbers=True, word_wrap=False,
                                             background_color="default"))
                        in_code = False
                        code_buffer = ""
                        buffer = tail
                    continue

                # Texte normal : petit flux lisible au lieu d'un gros bloc final.
                if len(buffer) >= 3 or "\n" in buffer:
                    console.print(buffer, end="", style="tilex.value", highlight=True)
                    buffer = ""
                    time.sleep(0.018)

            # Vide ce qui reste à la fin du stream.
            if in_code and code_buffer:
                # Si le modèle n'a pas fermé le bloc, on montre quand même le code coloré.
                console.print(Syntax(code_buffer.rstrip(), language, theme=CODE_STYLE,
                                     line_numbers=True, word_wrap=False,
                                     background_color="default"))
            elif buffer:
                console.print(buffer, end="", style="tilex.value", highlight=True)
            console.print()
    except Exception as exc:
        console.print(f"[tilex.error]Ollama indisponible : {exc}[/]")
        console.print(f"[tilex.warning]Vérifie Ollama puis : ollama pull {model}[/]")

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
    console.print(Syntax(p.read_text(errors="replace"), lexer, theme=CODE_STYLE, line_numbers=True, word_wrap=True))

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

def project_files():
    if not active_project:
        return []
    return sorted(
        p for p in active_project.rglob("*")
        if p.is_file() and ".git" not in p.parts and ".venv" not in p.parts
    )

def show_project_status():
    files = project_files()
    total_bytes = sum(p.stat().st_size for p in files)
    table = Table(title=f"PROJET • {active_project.name}", border_style="medium_purple1")
    table.add_column("INFO", style="bright_cyan")
    table.add_column("VALEUR", style="bright_green")
    table.add_row("Dossier", str(active_project))
    table.add_row("Fichiers", str(len(files)))
    table.add_row("Taille", f"{total_bytes / 1024:.1f} KB")
    table.add_row("Python", str(sum(1 for p in files if p.suffix == ".py")))
    console.print(table)

def show_project_files():
    files = project_files()
    table = Table(title=f"FICHIERS • {active_project.name}", border_style="bright_cyan")
    table.add_column("#", style="bright_yellow")
    table.add_column("Fichier", style="#39FF14")
    table.add_column("Type", style="bright_cyan")
    for i, p in enumerate(files, 1):
        table.add_row(str(i), str(p.relative_to(active_project)), p.suffix or "fichier")
    console.print(table)

def search_project(term):
    term = term.strip()
    if not term:
        console.print("[tilex.warning]Utilisation : /chercher TEXTE[/]")
        return
    results = []
    for p in project_files():
        try:
            for n, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                if term.lower() in line.lower():
                    results.append((str(p.relative_to(active_project)), n, line.strip()))
                    if len(results) >= 50:
                        break
        except Exception:
            pass
        if len(results) >= 50:
            break
    table = Table(title=f"RECHERCHE • {term}", border_style="dark_orange")
    table.add_column("Fichier", style="bright_cyan")
    table.add_column("Ligne", style="bright_yellow")
    table.add_column("Résultat", style="#39FF14")
    for path, line_no, line in results:
        table.add_row(path, str(line_no), line[:100])
    console.print(table if results else "[tilex.warning]Aucun résultat.[/]")

def show_project_tree():
    if not active_project:
        console.print("[tilex.warning]Aucun projet actif.[/]")
        return
    console.print(f"[bold #39FF14]{active_project.name}/[/]")
    paths = sorted(
        p for p in active_project.rglob("*")
        if ".git" not in p.parts and ".venv" not in p.parts
    )
    for p in paths[:200]:
        rel = p.relative_to(active_project)
        depth = len(rel.parts) - 1
        icon = "📁" if p.is_dir() else "📄"
        console.print("    " * depth + f"├── {icon} [bright_white]{p.name}[/]")
    if len(paths) > 200:
        console.print(f"[tilex.warning]… {len(paths) - 200} éléments supplémentaires non affichés.[/]")

def file_icon(path):
    if path.is_dir():
        return "📁"
    name = path.name.lower()
    if name.startswith("readme"):
        return "📖"
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg"}:
        return "🖼️"
    return "📄"

def explorer_text(selected=None, limit=34):
    if not active_project:
        return Text("Aucun projet actif", style="tilex.warning")
    out = Text()
    out.append(f"📁 {active_project.name}\n", style="bold #39FF14")
    paths = sorted(
        p for p in active_project.rglob("*")
        if ".git" not in p.parts and ".venv" not in p.parts
    )
    for p in paths[:limit]:
        rel = p.relative_to(active_project)
        depth = len(rel.parts) - 1
        is_selected = selected is not None and p.resolve() == selected.resolve()
        prefix = "▶ " if is_selected else "  "
        out.append("   " * depth + prefix + file_icon(p) + " ")
        out.append(str(rel.parts[-1]) + "\n",
                   style="bold #39FF14" if is_selected else "bright_white")
    if len(paths) > limit:
        out.append(f"… +{len(paths) - limit} fichiers/dossiers\n", style="tilex.comment")
    return out

def show_codex_workspace(filename=None, codex_message=None):
    if not active_project:
        console.print("[tilex.warning]Aucun projet actif.[/]")
        return

    selected = project_path(filename) if filename else None
    left = Panel(
        explorer_text(selected),
        title="[#39FF14]📁 FICHIERS[/]",
        border_style="#39FF14",
        width=max(24, console.width // 4),
    )

    if selected and selected.is_file():
        suffix = selected.suffix.lower()
        lexer_map = {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".html": "html", ".css": "css", ".json": "json",
            ".md": "markdown", ".sh": "bash", ".ps1": "powershell",
        }
        lexer = lexer_map.get(suffix, "text")
        try:
            body = Syntax(
                selected.read_text(encoding="utf-8", errors="replace"),
                lexer, theme=CODE_STYLE, line_numbers=True,
                word_wrap=False, background_color="default"
            )
        except Exception as exc:
            body = Text(f"Impossible d'ouvrir ce fichier : {exc}", style="tilex.error")
        title = f"[#39FF14]CODEX • ÉCRITURE / GÉNÉRATION[/] [bright_cyan]• {file_icon(selected)} {selected.name}[/] [#39FF14]+0 ajoutée[/] [#FF1744]-0 supprimée[/]"
    else:
        body = Text(justify="right")
        body.append("Sélectionne un fichier pour l'afficher ici.\n\n", style="bright_white")
        body.append(
            "",
            style="bright_white"
        )
        title = "[#39FF14]🤖 CODEX • ÉCRITURE / GÉNÉRATION[/]"

    right = Panel(body, title=title, border_style="bright_cyan")

    top_menu = Text(justify="center")
    top_menu.append(
        "LOCAL AI  •  CODING  •  DEVELOPER TERMINAL",
        style="bold #39FF14"
    )
    console.print(Panel(top_menu, border_style="bright_cyan", padding=(0, 1)))

    workspace_height = max(16, console.height - 17)
    left.height = workspace_height
    right.height = workspace_height

    tools_panel = Panel(
        Text("🤖 IA\n▶ RUN\n🌐 PREVIEW\n🔨 BUILD\n🧪 TESTS\n📜 LOGS\n📦 DÉPENDANCES\n🔀 GIT\n💾 SAUVEGARDES\n⚙ SETTINGS", style="bright_white"),
        title="[#39FF14]OUTILS[/]",
        border_style="dark_orange",
        height=workspace_height
    )

    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=1)
    grid.add_column(ratio=4)
    grid.add_column(ratio=1)
    grid.add_row(left, right, tools_panel)
    console.print(grid)

    input_hint = Text(justify="center")
    input_hint.append("✍ CODEX LOCAL › ", style="bold dark_orange")
    input_hint.append("écris ta commande ici", style="bright_white")
    console.print(Panel(
        input_hint,
        title="[dark_orange]ÉCRITURE UTILISATEUR[/]",
        border_style="dark_orange",
        padding=(0, 1),
        height=3
    ))


def show_diff_preview(filename, old_text, new_text):
    import difflib
    p = project_path(filename)
    diff = difflib.ndiff(old_text.splitlines(), new_text.splitlines())
    body = Text()
    for line in diff:
        if line.startswith("+ "):
            body.append("+ " + line[2:] + "\n", style="bold #39FF14")
        elif line.startswith("- "):
            body.append("- " + line[2:] + "\n", style="bold #FF1744")
        elif line.startswith("? "):
            continue
        else:
            body.append("  " + line[2:] + "\n", style="bright_white")
    left = Panel(explorer_text(p), title="[#39FF14]📁 FICHIERS[/]", border_style="#39FF14")
    right = Panel(body, title=f"[bright_cyan]📄 {p.name} • MODIFICATIONS[/]", border_style="bright_cyan")
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=1)
    grid.add_column(ratio=3)
    grid.add_row(left, right)
    console.print(grid)


def show_readme():
    if not active_project:
        console.print("[tilex.warning]Aucun projet actif.[/]")
        return
    readme = active_project / "README.md"
    if not readme.is_file():
        console.print("[tilex.warning]README.md introuvable.[/]")
        return
    console.print(Panel(
        readme.read_text(encoding="utf-8", errors="replace"),
        title=f"[#39FF14]README • {active_project.name}[/]",
        border_style="#39FF14"
    ))

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg"}

def project_images_dir():
    if not active_project:
        return None
    folder = active_project / "assets" / "images"
    folder.mkdir(parents=True, exist_ok=True)
    return folder

def list_project_images():
    folder = project_images_dir()
    if folder is None:
        console.print("[tilex.warning]Aucun projet actif.[/]")
        return []
    images = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS)
    table = Table(title="CODEX LOCAL • IMAGES", border_style="#39FF14")
    table.add_column("#", style="dark_orange")
    table.add_column("Image", style="#39FF14")
    table.add_column("Format", style="bright_cyan")
    table.add_column("Taille", style="bright_white")
    for n, p in enumerate(images, 1):
        table.add_row(str(n), p.name, p.suffix.lower().lstrip(".").upper(), f"{p.stat().st_size / 1024:.1f} KB")
    console.print(table if images else "[tilex.warning]Aucune image dans assets/images.[/]")
    return images

def import_project_image():
    folder = project_images_dir()
    if folder is None:
        console.print("[tilex.warning]Aucun projet actif.[/]")
        return
    raw = session.prompt("Chemin de l'image sur ton ordinateur › ").strip().strip('"')
    source = Path(raw).expanduser()
    if not source.is_file():
        console.print("[tilex.error]Image introuvable.[/]")
        return
    if source.suffix.lower() not in IMAGE_EXTENSIONS:
        console.print("[tilex.error]Format non accepté. Utilise PNG, JPG, JPEG, WEBP, GIF, BMP ou SVG.[/]")
        return
    target = folder / source.name
    if target.exists():
        console.print("[tilex.warning]Une image avec ce nom existe déjà. Import annulé.[/]")
        return
    shutil.copy2(source, target)
    relative = target.relative_to(active_project)
    console.print(f"[tilex.success]✓ Image ajoutée : {relative}[/]")
    console.print(f"[tilex.info]Chemin pour ton code : {relative.as_posix()}[/]")

def image_info():
    images = list_project_images()
    if not images:
        return
    try:
        n = int(session.prompt("Numéro de l'image › ").strip())
    except ValueError:
        console.print("[tilex.error]Numéro invalide.[/]")
        return
    if not 1 <= n <= len(images):
        console.print("[tilex.error]Numéro invalide.[/]")
        return
    p = images[n - 1]
    rel = p.relative_to(active_project)
    table = Table(title=f"IMAGE • {p.name}", border_style="bright_cyan")
    table.add_column("INFO", style="dark_orange")
    table.add_column("VALEUR", style="#39FF14")
    table.add_row("Nom", p.name)
    table.add_row("Format", p.suffix.lower().lstrip(".").upper())
    table.add_row("Taille", f"{p.stat().st_size / 1024:.1f} KB")
    table.add_row("Chemin local", str(p))
    table.add_row("Chemin code", rel.as_posix())
    console.print(table)

def images_lab():
    while True:
        console.print(Panel(
            "[dark_orange][01][/] [bright_white]AJOUTER UNE IMAGE[/]\n"
            "[dark_orange][02][/] [bright_white]VOIR LES IMAGES DU PROJET[/]\n"
            "[dark_orange][03][/] [bright_white]INFORMATIONS D'UNE IMAGE[/]\n"
            "[dark_orange][04][/] [bright_white]CHEMINS POUR LE CODE[/]\n"
            "[dark_orange][05][/] [bright_white]VISION IA LOCALE[/]\n"
            "[dark_orange][00][/] [#39FF14]RETOUR AU LABORATOIRE[/]",
            title="[#39FF14]CODEX LOCAL • IMAGES / ASSETS[/]",
            border_style="#39FF14"
        ))
        choice = session.prompt("IMAGE › ").strip()
        if choice == "00":
            return
        if choice == "01":
            import_project_image()
        elif choice == "02":
            list_project_images()
        elif choice == "03":
            image_info()
        elif choice == "04":
            images = list_project_images()
            for p in images:
                console.print(f"[tilex.info]{p.relative_to(active_project).as_posix()}[/]")
        elif choice == "05":
            console.print("[tilex.warning]VISION IA LOCALE : nécessite un modèle Ollama multimodal. Branchement prévu à l'étape suivante.[/]")
        else:
            console.print("[tilex.warning]Choix invalide.[/]")



class CodexLocalApp(App):
    CSS = """
    Screen { background: #000000; color: #ffffff; }
    #brand { height: 11; background: #000000; color: #39ff14; text-style: bold; content-align: left top; border: none; padding: 0 1; }
    #workspace { height: 1fr; }
    #files { width: 25%; background: #000000; color: #ffffff; border: solid #39ff14; }
    #files ListItem { background: #000000; color: #ffffff; }
    #files ListItem.--highlight { background: #001a00; color: #39ff14; }
    #center { width: 1fr; background: #000000; border: solid #00e5ff; }
    #tools { width: 18%; background: #000000; color: #ffffff; border: solid #ff7a00; padding: 0 1; }
    #tools Static { background: #000000; color: #ffffff; }
    #tools Button { width: 100%; height: 3; margin: 0; background: #000000; color: #ffffff; border: none; }
    #tools Button:focus { background: #001a00; color: #39ff14; text-style: bold; }
    #editor_title { height: 3; background: #000000; content-align: center middle; color: #39ff14; text-style: bold; }
    #editor { height: 1fr; background: #000000; color: #ffffff; }\n    #chat_log { height: 1fr; background: #000000; color: #ffffff; display: none; }
    #user_input { dock: bottom; height: 3; background: #000000; color: #ffffff; border: solid #ff7a00; }
    Footer { background: #000000; color: #ffffff; }
    """
    BINDINGS = [Binding("ctrl+s", "save_file", "Sauvegarder"), Binding("escape", "quit", "Retour")]

    def __init__(self, root):
        super().__init__()
        self.root = Path(root).resolve()
        self.current_path = None
        self._typing_text = ""
        self._typing_pos = 0
        self._typing_timer = None
        self._typing_stats = {"added": 0, "modified": 0, "deleted": 0}
        self._typing_chunk = 24
        self.mode = "codex"
        self.chat_history = []
        self._chat_typing_text = ""
        self._chat_typing_pos = 0
        self._chat_typing_timer = None
        self._codex_busy = False
        self._codex_queue = []

    def compose(self) -> ComposeResult:
        yield Static(ASCII_TI_LEX + "\n" + ASCII_CODEX + "\nLOCAL AI • CODING • DEVELOPER TERMINAL", id="brand")
        with Horizontal(id="workspace"):
            yield ListView(id="files")
            with Vertical(id="center"):
                yield Static("CODEX • ÉCRITURE / GÉNÉRATION", id="editor_title")
                yield TextArea("", id="editor", language="python", show_line_numbers=True)
                yield RichLog(id="chat_log", markup=True, wrap=True, auto_scroll=True)
            with Vertical(id="tools"):
                yield Static("🛠 OUTILS")
                yield Button("🤖 IA", id="tool_ai")
                yield Button("▶ RUN", id="tool_run")
                yield Button("🌐 PREVIEW", id="tool_preview")
                yield Button("🔨 BUILD", id="tool_build")
                yield Button("🧪 TESTS", id="tool_tests")
                yield Button("💾 SAUVEGARDER", id="tool_save")
        yield Input(placeholder="✍ CODEX LOCAL › écris ta commande ici…", id="user_input")
        yield Footer()

    def _set_editor_language(self, path):
        editor = self.query_one("#editor", TextArea)
        ext = Path(path).suffix.lower()
        languages = {
            ".py": "python",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ts": "typescript",
            ".tsx": "tsx",
            ".html": "html",
            ".htm": "html",
            ".css": "css",
            ".json": "json",
            ".md": "markdown",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".toml": "toml",
            ".sql": "sql",
            ".sh": "bash",
        }
        language = languages.get(ext)
        editor.language = language if language in editor.available_languages else None

    def on_mount(self):
        editor = self.query_one("#editor", TextArea)
        editor.register_theme(TI_LEX_TEXTAREA_THEME)
        editor.theme = "ti_lex_neon"
        view = self.query_one("#files", ListView)
        for p in project_files():
            rel = p.relative_to(self.root)
            item = ListItem(Label("📄 " + str(rel)))
            item.path = p
            view.append(item)

    def on_list_view_selected(self, event):
        self.action_save_file()
        p = getattr(event.item, "path", None)
        if p and p.is_file():
            self.current_path = p
            self._set_editor_language(p)
            self.query_one("#editor", TextArea).text = p.read_text(encoding="utf-8", errors="replace")
            self.query_one("#editor_title", Static).update("CODEX • ÉCRITURE / GÉNÉRATION  •  📄 " + p.name)
            self.query_one("#editor", TextArea).focus()

    def action_save_file(self):
        if self.current_path:
            text = self.query_one("#editor", TextArea).text
            self.current_path.write_text(text, encoding="utf-8")
            self.notify("Sauvegardé : " + self.current_path.name)

    def on_input_submitted(self, event):
        cmd = event.value.strip()
        event.input.value = ""
        if cmd.lower() == "/chat":
            self.mode = "chat"
            self.query_one("#editor", TextArea).display = False
            self.query_one("#chat_log", RichLog).display = True
            self.query_one("#editor_title", Static).update("CHAT IA • NEON • /codex POUR REVENIR")
            self.query_one("#editor", TextArea).text = "=== TI-LEX CHAT IA ===\n\nÉcris ton message en bas.\n/codex = retour au CODEX."
            self.query_one("#user_input", Input).placeholder = "💬 CHAT IA › écris ton message…"
            return
        if cmd.lower() == "/codex":
            self.mode = "codex"
            self.query_one("#chat_log", RichLog).display = False
            self.query_one("#editor", TextArea).display = True
            self.query_one("#editor_title", Static).update("CODEX • ÉCRITURE / GÉNÉRATION")
            self.query_one("#user_input", Input).placeholder = "✍ CODEX LOCAL › écris ta commande ici…"
            if self.current_path and self.current_path.is_file():
                self.query_one("#editor", TextArea).text = self.current_path.read_text(encoding="utf-8", errors="replace")
            else:
                self.query_one("#editor", TextArea).text = ""
            return
        if self.mode == "chat" and cmd:
            self.chat_history.append(("TOI", cmd))
            self._render_chat()
            self.run_worker(lambda: self._chat_in_background(cmd), thread=True, exclusive=True)
            return
        if cmd.lower().startswith("nouveau "):
            rel = cmd[8:].strip()
            target = (self.root / rel).resolve()
            if target == self.root or self.root not in target.parents:
                self.notify("Chemin refusé.", severity="error")
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch(exist_ok=True)
            self.notify("Fichier créé : " + str(target.relative_to(self.root)))
        elif cmd.lower() in {"save", "sauve", "sauvegarde"}:
            self.action_save_file()
        elif cmd:
            if self._codex_busy:
                self._codex_queue.append(cmd)
                self.notify(
                    "Commande ajoutée à la file • " + str(len(self._codex_queue)) + " en attente",
                    severity="information",
                )
                return
            self._start_codex_command(cmd)

    def _start_codex_command(self, cmd):
        self._codex_busy = True
        self.notify("CODEX travaille en arrière-plan…")
        self.run_worker(lambda: self._build_in_background(cmd), thread=True, exclusive=False)

    def _run_next_codex_command(self):
        if self._codex_queue:
            next_cmd = self._codex_queue.pop(0)
            self._start_codex_command(next_cmd)
        else:
            self._codex_busy = False

    def _style_chat_message(self, prefix, message, user=False):
        line = Text(prefix, style="bold #FF7A00" if user else "bold #00E5FF")
        if user:
            line.append(message, style="#FFFFFF")
            return line

        swear_pattern = re.compile(
            r"\\b(tabarnak|tabarnac|tabarnouche|c[âa]lice|calisse|c[âa]lisse|crisse|esti|osti|sacrament|viarge)\\b",
            re.IGNORECASE,
        )
        chunks = message.split("`")
        for index, chunk in enumerate(chunks):
            if index % 2:
                line.append(chunk, style="bold #FF7A00")
                continue
            pos = 0
            for match in swear_pattern.finditer(chunk):
                if match.start() > pos:
                    line.append(chunk[pos:match.start()], style="#87CEFA")
                line.append(match.group(0), style="bold #FF1744")
                pos = match.end()
            if pos < len(chunk):
                line.append(chunk[pos:], style="#87CEFA")
        return line

    def _render_chat(self, partial_ai=None):
        log = self.query_one("#chat_log", RichLog)
        log.clear()
        log.write(Text("=== TI-LEX CHAT IA ===", style="bold #39FF14"))
        for who, message in self.chat_history[-30:]:
            log.write(self._style_chat_message(
                "TOI › " if who == "TOI" else "IA › ",
                message,
                user=(who == "TOI"),
            ))
        if partial_ai is not None:
            log.write(self._style_chat_message("IA › ", partial_ai, user=False))

    def _chat_in_background(self, message):
        history = "\n".join(who + ": " + text for who, text in self.chat_history[-12:])
        prompt = (
            "Tu es TI-LEX CHAT, assistant de programmation local québécois. "
            "Parle avec un joual québécois très marqué, populaire, direct, comique et énergique, "
            "avec des contractions naturelles (chu, t'es, y'a, c'te, icitte), des expressions du Québec "
            "et quelques sacres légers quand le contexte s'y prête. Garde un ton de gars de garage/chantier, "
            "grande gueule mais sympathique. Ne copie aucune réplique connue ni catchphrase d'un personnage existant. "
            "Reste quand même clair et utile techniquement. "
            "Ceci est un chat: ne crée et ne modifie aucun fichier.\n\n"
            + history + "\nIA:"
        )
        try:
            response = requests.post(
                "http://127.0.0.1:11434/api/generate",
                json={"model": config["model"], "prompt": prompt, "stream": False},
                timeout=600,
            )
            response.raise_for_status()
            answer = response.json().get("response", "").strip()
        except Exception as exc:
            answer = "Erreur CHAT IA: " + str(exc)
        self.call_from_thread(self._finish_chat, answer)

    def _finish_chat(self, answer):
        if self.mode != "chat":
            return
        if self._chat_typing_timer:
            self._chat_typing_timer.stop()
        self._chat_typing_text = answer
        self._chat_typing_pos = 0
        self.query_one("#editor_title", Static).update(
            "CHAT IA • ÉCRITURE FLUIDE • /codex POUR REVENIR"
        )
        self._chat_typing_timer = self.set_interval(0.035, self._chat_typing_tick)
        self.query_one("#user_input", Input).focus()

    def _chat_typing_tick(self):
        if self.mode != "chat":
            if self._chat_typing_timer:
                self._chat_typing_timer.stop()
                self._chat_typing_timer = None
            return
        if self._chat_typing_pos >= len(self._chat_typing_text):
            if self._chat_typing_timer:
                self._chat_typing_timer.stop()
                self._chat_typing_timer = None
            self.chat_history.append(("IA", self._chat_typing_text))
            self._render_chat()
            self.query_one("#editor_title", Static).update(
                "CHAT IA • PRÊT • /codex POUR REVENIR"
            )
            return
        self._chat_typing_pos = min(self._chat_typing_pos + 2, len(self._chat_typing_text))
        self._render_chat(self._chat_typing_text[:self._chat_typing_pos])


    def _build_in_background(self, request):
        try:
            engine = CodexEngine(self.root, model=config["model"])
            result = engine.build(request)
            self.call_from_thread(self._finish_build, result, engine.last_content, engine.last_stats)
        except Exception as exc:
            self.call_from_thread(self._finish_build_error, str(exc))

    def _finish_build_error(self, error):
        self.notify("Erreur moteur CODEX: " + error, severity="error")
        self.query_one("#user_input", Input).focus()
        self._run_next_codex_command()

    def _finish_build(self, result, generated_code, stats):
        self.notify(result.message, severity="information" if result.ok else "error")
        if not result.ok:
            self.query_one("#user_input", Input).focus()
            self._run_next_codex_command()
            return

        view = self.query_one("#files", ListView)
        view.clear()
        for p in sorted(
            x for x in self.root.rglob("*")
            if x.is_file() and ".git" not in x.parts
            and ".venv" not in x.parts and ".tilex" not in x.parts
        ):
            item = ListItem(Label("📄 " + str(p.relative_to(self.root))))
            item.path = p
            view.append(item)

        if result.changed:
            target = (self.root / result.changed[-1]).resolve()
            if target.is_file():
                self.current_path = target
                self._set_editor_language(target)
                code = generated_code or target.read_text(encoding="utf-8", errors="replace")
                self.query_one("#editor", TextArea).text = code
                s = stats or {"added": 0, "modified": 0, "deleted": 0}
                self.query_one("#editor_title", Static).update(
                    "CODEX • SAUVEGARDÉ • 📄 " + target.name
                    + f" • +{s['added']} ajoutées"
                    + f" • ~{s['modified']} modifiées"
                    + f" • -{s['deleted']} supprimées"
                )

        self.query_one("#user_input", Input).focus()
        self._run_next_codex_command()

    def _start_typing(self, code, stats=None):
        if self._typing_timer:
            self._typing_timer.stop()
            self._typing_timer = None
        self._typing_text = code
        self._typing_pos = 0
        self._typing_stats = stats or {"added": 0, "modified": 0, "deleted": 0}
        editor = self.query_one("#editor", TextArea)
        editor.text = ""

        size = len(code)
        if size > 24000:
            editor.text = code
            self._typing_pos = size
            self._finish_typing_status()
            return
        elif size > 12000:
            self._typing_chunk = 240
        elif size > 5000:
            self._typing_chunk = 120
        elif size > 1500:
            self._typing_chunk = 60
        else:
            self._typing_chunk = 24

        self._typing_timer = self.set_interval(0.06, self._typing_tick)

    def _finish_typing_status(self):
        self._codex_busy = False
        if self.current_path:
            s = self._typing_stats
            self.query_one("#editor_title", Static).update(
                "CODEX • SAUVEGARDÉ • 📄 " + self.current_path.name
                + f" • +{s['added']} ajoutées • ~{s['modified']} modifiées • -{s['deleted']} supprimées"
            )

    def _typing_tick(self):
        if self._typing_pos >= len(self._typing_text):
            if self._typing_timer:
                self._typing_timer.stop()
                self._typing_timer = None
            self._finish_typing_status()
            return

        self._typing_pos = min(
            self._typing_pos + self._typing_chunk,
            len(self._typing_text),
        )
        self.query_one("#editor", TextArea).text = self._typing_text[:self._typing_pos]

        if self.current_path:
            s = self._typing_stats
            progress = self._typing_pos / max(1, len(self._typing_text))
            added_now = round(s["added"] * progress)
            modified_now = round(s["modified"] * progress)
            self.query_one("#editor_title", Static).update(
                "CODEX • ÉCRITURE TURBO • 📄 " + self.current_path.name
                + f" • +{added_now}/{s['added']} ajoutées"
                + f" • ~{modified_now}/{s['modified']} modifiées"
                + f" • -{s['deleted']} supprimées"
            )

    def on_button_pressed(self, event):
        if event.button.id == "tool_save":
            self.action_save_file()
        elif event.button.id == "tool_run":
            self.action_save_file()
            if self.current_path and self.current_path.suffix == ".py":
                subprocess.Popen([sys.executable, str(self.current_path)], cwd=str(self.root))
                self.notify("Exécution : " + self.current_path.name)
        else:
            self.notify(str(event.button.label) + " sélectionné")

    def on_unmount(self):
        self.action_save_file()

def codex_local_lab():
    if not active_project:
        console.print("[tilex.warning]Sélectionne d'abord un projet avec 01 ou crée-en un avec 02.[/]")
        return
    app = CodexLocalApp(active_project)
    app.run()


def show_dev_menu():
    project_name = active_project.name if active_project else "AUCUN"
    console.print(Panel(
        "[dark_orange][01][/] [bright_white]CONTINUER / CHANGER DE PROJET[/]\n"
        "[dark_orange][02][/] [bright_white]NOUVEAU PROJET[/]\n"
        "[dark_orange][03][/] [bright_white]CODEX LOCAL • LABORATOIRE[/]\n"
        "[dark_orange][04][/] [bright_white]ASSISTANT IA[/]\n"
        "[dark_orange][05][/] [bright_white]EXÉCUTER LE PROJET[/]\n"
        "[dark_orange][06][/] [bright_white]RECHERCHER DANS LE CODE[/]\n"
        "[dark_orange][07][/] [bright_white]TESTS DU PROJET[/]\n"
        "[dark_orange][08][/] [bright_white]DÉPENDANCES PYTHON[/]\n"
        "[dark_orange][09][/] [bright_white]GIT LOCAL[/]\n"
        "[dark_orange][10][/] [bright_white]ÉTAT DU PROJET[/]\n"
        "[dark_orange][11][/] [bright_white]HISTORIQUE / SAUVEGARDES[/]\n"
        "[dark_orange][12][/] [bright_white]CONFIGURATION TI-LEX[/]\n"
        "[bold #FF1744][13] SUPPRIMER UN PROJET[/]\n"
        "[dark_orange][14][/] [bright_white]AIDE[/]\n"
        "[dark_orange][00][/] [bright_white]QUITTER[/]\n\n"
        f"[#39FF14]PROJET ACTIF › {project_name}[/]",
        title="[#39FF14]TI-LEX CODEX • MENU DÉVELOPPEUR[/]",
        border_style="#39FF14"
    ))


def delete_project():
    global active_project
    root = projects_root().resolve()
    projects = sorted([p for p in root.iterdir() if p.is_dir()])
    if not projects:
        console.print("[tilex.warning]Aucun projet à supprimer.[/]")
        return

    table = Table(title="SUPPRIMER UN PROJET", border_style="#39FF14")
    table.add_column("#", style="dark_orange")
    table.add_column("Projet", style="#39FF14")
    for n, project in enumerate(projects, 1):
        table.add_row(str(n), project.name)
    console.print(table)

    try:
        choice = int(session.prompt("Numéro du projet › ").strip())
    except ValueError:
        console.print("[tilex.error]Choix invalide. Suppression annulée.[/]")
        return
    if choice < 1 or choice > len(projects):
        console.print("[tilex.error]Choix invalide. Suppression annulée.[/]")
        return

    target = projects[choice - 1].resolve()
    if target.parent != root:
        console.print("[tilex.error]Protection TI-LEX : chemin refusé.[/]")
        return

    console.print(f"[tilex.danger]ATTENTION : {target.name} sera supprimé complètement.[/]")
    typed_name = session.prompt("Confirmation 1 - écris exactement le nom du projet › ").strip()
    if typed_name != target.name:
        console.print("[tilex.warning]Nom incorrect. Suppression annulée.[/]")
        return

    final = session.prompt("Confirmation 2 - écris OUI pour supprimer › ").strip()
    if final != "OUI":
        console.print("[tilex.warning]Suppression annulée. Aucun fichier supprimé.[/]")
        return

    shutil.rmtree(target)
    if active_project and active_project.resolve() == target:
        active_project = None
    console.print(f"[tilex.success]✓ Projet supprimé : {target.name}[/]")

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
    global active_project
    console.clear()
    show_logo()
    if not (Path.home() / ".ti_lex_codex" / "config.json").exists():
        choose_font()

    console.print("[tilex.info]Bienvenue dans TI-LEX CODEX. Sélectionne une option du menu développeur.[/]")
    show_dev_menu()

    while True:
        project_label = active_project.name if active_project else "AUCUN-PROJET"
        cmd = session.prompt(f"TI-LEX CODEX [{project_label}] › ").strip()
        if not cmd:
            continue

        if cmd in ("00", "/quitter", "-quitter"):
            break
        elif cmd in ("01", "/projets", "-projets"):
            active_project = None
            startup_menu()
            show_dev_menu()
        elif cmd in ("02", "/nouveau", "-nouveau"):
            name = session.prompt("Nom du projet › ").strip()
            create_project(name)
            show_dev_menu()
        elif cmd in ("03", "/lab", "-lab"):
            codex_local_lab()
            show_dev_menu()
        elif cmd in ("/fichiers", "-fichiers"):
            if active_project:
                show_project_files()
            else:
                console.print("[tilex.warning]Sélectionne d'abord un projet avec 01 ou crée-en un avec 02.[/]")
        elif cmd in ("04", "/chat", "-chat"):
            console.print("[tilex.info]MODE CHAT • /retour pour revenir au projet[/]")
            chat_loop(False)
            show_dev_menu()
        elif cmd == "05":
            if not active_project:
                console.print("[tilex.warning]Sélectionne d'abord un projet.[/]")
            else:
                filename = session.prompt("Fichier Python à exécuter › ").strip()
                run_file(filename)
        elif cmd == "06":
            if not active_project:
                console.print("[tilex.warning]Sélectionne d'abord un projet.[/]")
            else:
                term = session.prompt("Texte à rechercher › ").strip()
                search_project(term)
        elif cmd == "07":
            console.print("[tilex.warning]Module TESTS en préparation.[/]")
        elif cmd == "08":
            console.print("[tilex.warning]Module DÉPENDANCES en préparation.[/]")
        elif cmd == "09":
            console.print("[tilex.warning]Module GIT LOCAL en préparation.[/]")
        elif cmd in ("10", "/etat", "-etat"):
            if active_project:
                show_project_status()
            else:
                console.print("[tilex.warning]Aucun projet actif.[/]")
        elif cmd == "11":
            console.print("[tilex.warning]Module HISTORIQUE / SAUVEGARDES en préparation.[/]")
        elif cmd in ("12", "/police", "-police"):
            choose_font()
        elif cmd in ("13", "/supprimer", "-supprimer"):
            delete_project()
            show_dev_menu()
        elif cmd in ("14", "/aide", "-aide"):
            console.print(Panel(HELP, title="TI-LEX CODEX • AIDE", border_style="#39FF14"))
        elif cmd in ("/menu", "-menu"):
            show_dev_menu()
        elif cmd.startswith("/ouvrir ") or cmd.startswith("-ouvrir "):
            show_file(cmd.split(" ", 1)[1].strip())
        elif cmd.startswith("/explique ") or cmd.startswith("-explique "):
            ai_file(cmd.split(" ", 1)[1].strip(), "Explique")
        elif cmd.startswith("/corrige ") or cmd.startswith("-corrige "):
            ai_file(cmd.split(" ", 1)[1].strip(), "Analyse les erreurs et propose une version corrigée de")
        elif cmd.startswith("/run ") or cmd.startswith("-run "):
            run_file(cmd.split(" ", 1)[1].strip())
        elif cmd.startswith("/chercher ") or cmd.startswith("-chercher "):
            search_project(cmd.split(" ", 1)[1])
        elif cmd.startswith("-projet "):
            create_project(cmd[8:].strip())
        elif cmd.startswith("-selection "):
            select_project(cmd[11:].strip())
        else:
            if not active_project:
                console.print("[tilex.warning]Choisis 01 pour continuer un projet ou 02 pour en créer un.[/]")
            else:
                ollama(project_context() + "\nDemande de l'utilisateur dans le projet actif : " + cmd)

if __name__ == "__main__":
    main()
