from __future__ import annotations

import os
import py_compile
import shutil
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

import requests
from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPixmap, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QProgressBar,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from codex_engine import CodexEngine
from config import load_config


IGNORE_DIRS = {".git", ".venv", "venv", "__pycache__", ".tilex", "node_modules"}
TEXT_EXTENSIONS = {
    ".py", ".pyw", ".js", ".jsx", ".ts", ".tsx", ".html", ".htm", ".css", ".scss",
    ".json", ".md", ".txt", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".sh", ".ps1",
    ".sql", ".xml", ".env", ".example", ".c", ".h", ".cpp", ".hpp", ".java", ".go", ".rs",
}


class PythonHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.rules = []

        def fmt(color: str, bold: bool = False):
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(QFont.Bold)
            return f

        import re

        keyword = fmt("#ff7a00", True)
        builtin = fmt("#00f7ff")
        string = fmt("#39ff14")
        comment = fmt("#6b7280")
        number = fmt("#ffe600")
        decorator = fmt("#ff2bd6")

        keywords = (
            "False None True and as assert async await break class continue def del elif else "
            "except finally for from global if import in is lambda nonlocal not or pass raise "
            "return try while with yield"
        ).split()

        for word in keywords:
            self.rules.append((re.compile(rf"\b{word}\b"), keyword))

        for word in ("print", "len", "str", "int", "float", "dict", "list", "set", "tuple", "Path", "open"):
            self.rules.append((re.compile(rf"\b{word}\b"), builtin))

        self.rules.extend([
            (re.compile(r"#[^\n]*"), comment),
            (re.compile(r"\b\d+(?:\.\d+)?\b"), number),
            (re.compile(r"@[A-Za-z_][A-Za-z0-9_\.]*"), decorator),
            (re.compile(r"'[^'\n]*'"), string),
            (re.compile(r'"[^"\n]*"'), string),
        ])

    def highlightBlock(self, text: str):
        for pattern, text_format in self.rules:
            for match in pattern.finditer(text):
                self.setFormat(match.start(), match.end() - match.start(), text_format)


class DiffHighlighter(QSyntaxHighlighter):
    """Couleurs Git diff: ajouts verts, suppressions rouges, en-têtes cyan."""
    def __init__(self, document):
        super().__init__(document)
        self.added = QTextCharFormat()
        self.added.setForeground(QColor("#39ff14"))
        self.removed = QTextCharFormat()
        self.removed.setForeground(QColor("#ff5252"))
        self.header = QTextCharFormat()
        self.header.setForeground(QColor("#00efff"))
        self.header.setFontWeight(QFont.Bold)
        self.file = QTextCharFormat()
        self.file.setForeground(QColor("#ff9d21"))
        self.file.setFontWeight(QFont.Bold)

    def highlightBlock(self, text: str):
        if text.startswith("+++ ") or text.startswith("--- ") or text.startswith("@@"):
            self.setFormat(0, len(text), self.header)
        elif text.startswith("+") and not text.startswith("+++"):
            self.setFormat(0, len(text), self.added)
        elif text.startswith("-") and not text.startswith("---"):
            self.setFormat(0, len(text), self.removed)
        elif text.startswith("FILE "):
            self.setFormat(0, len(text), self.file)


class CodexWorker(QObject):
    status = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, root: Path, request: str, model: str):
        super().__init__()
        self.root = root
        self.request = request
        self.model = model

    def run(self):
        try:
            engine = CodexEngine(
                self.root,
                model=self.model,
                status=lambda msg: self.status.emit(str(msg)),
            )
            result = engine.build(self.request)
            self.finished.emit((
                result,
                {
                    "outputs": dict(engine.last_outputs),
                    "stats": dict(engine.last_stats_by_file),
                    "diffs": dict(engine.last_diffs),
                },
            ))
        except BaseException as exc:
            # Empêche une erreur du moteur de tuer toute l'interface graphique.
            try:
                state_dir = self.root / ".tilex"
                state_dir.mkdir(parents=True, exist_ok=True)
                (state_dir / "gui_worker_error.log").write_text(
                    traceback.format_exc(),
                    encoding="utf-8",
                )
            except Exception:
                pass
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class TiLexCodexWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = load_config()
        self.project_root = Path.cwd().resolve()
        self.current_file: Path | None = None
        self.worker_thread: QThread | None = None
        self.worker: CodexWorker | None = None
        self.last_codex_request = ""
        self.last_right_panel_index = 0
        self.typewriter_timer = QTimer(self)
        self.typewriter_timer.setInterval(12)
        self.typewriter_timer.timeout.connect(self._typewriter_step)
        self.typewriter_text = ""
        self.typewriter_index = 0
        self.typewriter_chunk = 1

        # Animations natives PySide6 (aucun HTML).
        self.thinking_frames = ["◐", "◓", "◑", "◒"]
        self.thinking_index = 0
        self.engine_stage_text = "MOTEUR DE RÉFLEXION  •  PRÊT"
        self.thinking_timer = QTimer(self)
        self.thinking_timer.setInterval(140)
        self.thinking_timer.timeout.connect(self._animate_thinking)

        self.panel_animation = QPropertyAnimation(self)
        self.panel_animation.setDuration(240)
        self.panel_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.setWindowTitle("TI-LEX CODEX • IA Codex Marceau")
        self.resize(1680, 980)
        self.setMinimumSize(1180, 720)

        self._build_ui()
        self._apply_theme()
        self._load_project(self.project_root)
        self._refresh_ollama_status()

    def _build_ui(self):
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(7)

        # ===== HEADER =====
        header = QFrame(objectName="header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 10, 18, 10)
        header_layout.setSpacing(14)

        brand_box = QVBoxLayout()
        brand_box.setSpacing(0)
        brand = QLabel("▲  TI-LEX CODEX")
        brand.setObjectName("brand")
        subtitle = QLabel("IA Codex Marceau")
        subtitle.setObjectName("subtitle")
        brand_box.addWidget(brand)
        brand_box.addWidget(subtitle)

        header_layout.addLayout(brand_box)
        header_layout.addStretch(1)

        header_nav = QLabel("Développer   •   Analyser   •   Automatiser   •   Sans limites")
        header_nav.setObjectName("headerNav")
        header_nav.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(header_nav, 2)

        header_layout.addStretch(1)

        self.ollama_label = QLabel("● Ollama : vérification…")
        self.ollama_label.setObjectName("ollama")
        self.ollama_label.setMinimumWidth(240)
        header_layout.addWidget(self.ollama_label)

        root_layout.addWidget(header)

        # ===== MOTEUR DE RÉFLEXION =====
        engine_panel = QFrame(objectName="enginePanel")
        engine_layout = QHBoxLayout(engine_panel)
        engine_layout.setContentsMargins(8, 5, 10, 5)
        engine_layout.setSpacing(10)

        # Démon visuel accroché au moteur de réflexion.
        self.reflexion_avatar = QLabel()
        self.reflexion_avatar.setObjectName("reflexionAvatar")
        self.reflexion_avatar.setAlignment(Qt.AlignCenter)
        self.reflexion_avatar.setFixedSize(220, 140)

        demon_candidates = [
            Path(__file__).resolve().parent / "assets" / "reflexion_demon.png",
            Path(__file__).resolve().parent / "assets" / "tilex_al.png",
        ]
        demon_pixmap = QPixmap()
        for candidate in demon_candidates:
            if candidate.is_file() and demon_pixmap.load(str(candidate)):
                break

        if not demon_pixmap.isNull():
            self.reflexion_avatar.setPixmap(
                demon_pixmap.scaled(
                    205, 125,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            self.reflexion_avatar.setText("😈")

        engine_text = QVBoxLayout()
        engine_text.setContentsMargins(0, 0, 0, 0)
        engine_text.setSpacing(1)

        self.engine_status = QLabel("● MOTEUR DE RÉFLEXION  •  PRÊT")
        self.engine_status.setObjectName("engineStatus")

        self.engine_detail = QLabel("En attente d’une commande…")
        self.engine_detail.setObjectName("engineDetail")

        self.engine_flow = QLabel(
            "ANALYSE  →  PLAN  →  EXÉCUTION  →  ÉCRITURE  →  VALIDATION  →  README"
        )
        self.engine_flow.setObjectName("engineFlow")

        engine_text.addWidget(self.engine_status)
        engine_text.addWidget(self.engine_detail)
        engine_text.addWidget(self.engine_flow)

        self.engine_progress = QProgressBar()
        self.engine_progress.setObjectName("engineProgress")
        self.engine_progress.setRange(0, 100)
        self.engine_progress.setValue(0)
        self.engine_progress.setTextVisible(False)
        self.engine_progress.setMaximumWidth(180)

        self.btn_reopen_panel = QPushButton("▶  PANNEAU")
        self.btn_reopen_panel.setObjectName("reopenPanelButton")
        self.btn_reopen_panel.setMaximumWidth(130)
        self.btn_reopen_panel.clicked.connect(self._reopen_right_panel)

        engine_layout.addWidget(self.reflexion_avatar)
        engine_layout.addLayout(engine_text, 1)
        engine_layout.addWidget(self.engine_progress)
        engine_layout.addWidget(self.btn_reopen_panel)
        root_layout.addWidget(engine_panel)

        # ===== MAIN AREA =====
        main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter = main_splitter
        main_splitter.setChildrenCollapsible(False)
        main_splitter.setHandleWidth(4)

        # LEFT
        left = QFrame(objectName="panel")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(7)

        left_title = QLabel("📁  PROJET")
        left_title.setObjectName("sectionTitle")
        left_layout.addWidget(left_title)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setObjectName("projectTree")
        self.tree.itemDoubleClicked.connect(self._open_tree_item)
        left_layout.addWidget(self.tree, 8)

        self.btn_new = QPushButton("＋  Nouveau projet")
        self.btn_new.setObjectName("newProjectButton")
        self.btn_new.clicked.connect(self.new_project)
        left_layout.addWidget(self.btn_new)

        open_project = QPushButton("📂  Ouvrir projet")
        open_project.setObjectName("openProjectButton")
        open_project.clicked.connect(self.open_project)
        left_layout.addWidget(open_project)

        self.project_label = QLabel("")
        self.project_label.setWordWrap(True)
        self.project_label.setObjectName("muted")
        left_layout.addWidget(self.project_label)

        # Compact language badge only: maximum space for project files.
        self.lang_badge = QLabel("📄  AUCUN FICHIER")
        self.lang_badge.setObjectName("langBadge")
        self.lang_badge.setAlignment(Qt.AlignCenter)
        self.lang_badge.setMaximumHeight(38)
        left_layout.addWidget(self.lang_badge)

        # CENTER
        center = QFrame(objectName="panel")
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(7, 7, 7, 7)
        center_layout.setSpacing(5)

        self.file_title = QLabel("📄 Aucun fichier ouvert")
        self.file_title.setObjectName("tabTitle")
        center_layout.addWidget(self.file_title)

        self.editor = QPlainTextEdit()
        self.editor.setObjectName("editor")
        self.editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        pretty_font = QFont("Ink Free")
        pretty_font.setPointSize(14)
        pretty_font.setWeight(QFont.Medium)
        self.editor.setFont(pretty_font)
        self.highlighter = PythonHighlighter(self.editor.document())
        center_layout.addWidget(self.editor, 1)

        # RIGHT - panneaux Codex repliables
        self.right_panel = QFrame(objectName="panel")
        right_layout = QVBoxLayout(self.right_panel)
        right_layout.setContentsMargins(8, 8, 8, 8)
        right_layout.setSpacing(7)

        right_tabs = QHBoxLayout()
        right_tabs.setSpacing(5)

        self.btn_panel_tools = QPushButton("1  OUTILS")
        self.btn_panel_tools.setObjectName("panelTabButton")
        self.btn_panel_tools.clicked.connect(lambda: self._show_right_panel(0))

        self.btn_panel_results = QPushButton("2  RÉSULTATS")
        self.btn_panel_results.setObjectName("panelTabButton")
        self.btn_panel_results.clicked.connect(lambda: self._show_right_panel(1))

        self.btn_panel_close = QPushButton("✕")
        self.btn_panel_close.setObjectName("panelCloseButton")
        self.btn_panel_close.setMaximumWidth(42)
        self.btn_panel_close.clicked.connect(self._close_right_panel)

        right_tabs.addWidget(self.btn_panel_tools)
        right_tabs.addWidget(self.btn_panel_results)
        right_tabs.addStretch(1)
        right_tabs.addWidget(self.btn_panel_close)
        right_layout.addLayout(right_tabs)

        self.right_stack = QStackedWidget()
        self.right_stack.setObjectName("rightStack")

        # Panneau 1 - outils + sortie
        tools_page = QWidget()
        tools_layout = QVBoxLayout(tools_page)
        tools_layout.setContentsMargins(0, 0, 0, 0)
        tools_layout.setSpacing(7)

        tools_title = QLabel("🔧  PANNEAU 1 • OUTILS")
        tools_title.setObjectName("sectionTitle")
        tools_layout.addWidget(tools_title)

        self.btn_run = self._tool_button("▶  Lancer                         F5", self.run_current)
        self.btn_save = self._tool_button("💾  Sauvegarder              Ctrl+S", self.save_current)
        self.btn_test = self._tool_button("🧪  Tester                       Ctrl+T", self.test_current)
        self.btn_build = self._tool_button("⬢  Build                        Ctrl+B", self.build_current)
        self.btn_open = self._tool_button("📂  Ouvrir projet             Ctrl+O", self.open_project)
        self.btn_zip = self._tool_button("🗜  Créer ZIP                  Ctrl+Z", self.create_zip)

        for btn in (
            self.btn_run, self.btn_save, self.btn_test,
            self.btn_build, self.btn_open, self.btn_zip
        ):
            tools_layout.addWidget(btn)

        status_title = QLabel("🖥  STATUT / SORTIE")
        status_title.setObjectName("sectionTitle")
        tools_layout.addWidget(status_title)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setObjectName("output")
        self.output.setMaximumBlockCount(1000)
        self.output.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        tools_layout.addWidget(self.output, 1)

        clear_btn = QPushButton("🗑  Effacer")
        clear_btn.setObjectName("clearButton")
        clear_btn.clicked.connect(self.output.clear)
        tools_layout.addWidget(clear_btn)

        # Panneau 2 - résultats / diff réel
        results_page = QWidget()
        results_layout = QVBoxLayout(results_page)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(7)

        results_title = QLabel("±  PANNEAU 2 • RÉSULTATS CODEX")
        results_title.setObjectName("sectionTitle")
        results_layout.addWidget(results_title)

        self.results_summary = QLabel("Aucune modification pour le moment.")
        self.results_summary.setObjectName("resultsSummary")
        self.results_summary.setWordWrap(True)
        results_layout.addWidget(self.results_summary)

        self.diff_view = QPlainTextEdit()
        self.diff_view.setObjectName("diffView")
        self.diff_view.setReadOnly(True)
        self.diff_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.diff_view.setPlaceholderText(
            "Les lignes ajoutées (+) et supprimées (-) apparaîtront ici après une commande CODEX."
        )
        self.diff_highlighter = DiffHighlighter(self.diff_view.document())
        results_layout.addWidget(self.diff_view, 1)

        self.right_stack.addWidget(tools_page)
        self.right_stack.addWidget(results_page)
        right_layout.addWidget(self.right_stack, 1)

        main_splitter.addWidget(left)
        main_splitter.addWidget(center)
        main_splitter.addWidget(self.right_panel)
        main_splitter.setStretchFactor(0, 2)
        main_splitter.setStretchFactor(1, 7)
        main_splitter.setStretchFactor(2, 3)
        main_splitter.setSizes([270, 1030, 380])

        root_layout.addWidget(main_splitter, 1)

        # ===== BOTTOM COMMAND BAR =====
        bottom = QFrame(objectName="commandBar")
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(10, 7, 10, 7)
        bottom_layout.setSpacing(9)

        prompt_box = QVBoxLayout()
        prompt_box.setSpacing(4)
        prompt_title = QLabel("💬  Commande Codex")
        prompt_title.setObjectName("sectionTitle")
        self.prompt = QLineEdit()
        self.prompt.setObjectName("prompt")
        self.prompt.setPlaceholderText("Ex : ajoute une fonction de validation dans config.py")
        self.prompt.returnPressed.connect(self.send_codex)
        prompt_box.addWidget(prompt_title)
        prompt_box.addWidget(self.prompt)

        send = QPushButton("➤  Envoyer\nCtrl+Entrée")
        send.setObjectName("sendButton")
        send.clicked.connect(self.send_codex)

        mode_box = QVBoxLayout()
        mode_box.setSpacing(4)
        mode_title = QLabel("⚙  MODE ACTIF")
        mode_title.setObjectName("sectionTitle")
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["AUTO", "PRO", "DIRECT"])
        mode_box.addWidget(mode_title)
        mode_box.addWidget(self.mode_combo)

        bottom_layout.addLayout(prompt_box, 1)
        bottom_layout.addWidget(send)
        bottom_layout.addLayout(mode_box)

        root_layout.addWidget(bottom)
        self.setCentralWidget(root)

        self._show_right_panel(0)
        self.btn_reopen_panel.setText("◀  PANNEAU")

        self.btn_save.setShortcut(QKeySequence("Ctrl+S"))
        self.btn_run.setShortcut(QKeySequence("F5"))
        self.btn_test.setShortcut(QKeySequence("Ctrl+T"))
        self.btn_build.setShortcut(QKeySequence("Ctrl+B"))
        self.btn_open.setShortcut(QKeySequence("Ctrl+O"))

    def _show_right_panel(self, index: int):
        self.last_right_panel_index = index
        self.right_stack.setCurrentIndex(index)

        self.btn_panel_tools.setProperty("active", index == 0)
        self.btn_panel_results.setProperty("active", index == 1)
        for button in (self.btn_panel_tools, self.btn_panel_results):
            button.style().unpolish(button)
            button.style().polish(button)

        if not self.right_panel.isVisible():
            self.right_panel.show()
            self.right_panel.setMaximumWidth(0)
            if hasattr(self, "btn_reopen_panel"):
                self.btn_reopen_panel.setText("◀  PANNEAU")

            self.panel_animation.stop()
            self.panel_animation.setTargetObject(self.right_panel)
            self.panel_animation.setPropertyName(b"maximumWidth")
            self.panel_animation.setStartValue(0)
            self.panel_animation.setEndValue(430)
            try:
                self.panel_animation.finished.disconnect()
            except RuntimeError:
                pass
            self.panel_animation.start()
        else:
            self.right_panel.setMaximumWidth(16777215)

    def _reopen_right_panel(self):
        self._show_right_panel(self.last_right_panel_index)

    def _close_right_panel(self):
        if not self.right_panel.isVisible():
            return

        width = max(1, self.right_panel.width())
        self.panel_animation.stop()
        self.panel_animation.setTargetObject(self.right_panel)
        self.panel_animation.setPropertyName(b"maximumWidth")
        self.panel_animation.setStartValue(width)
        self.panel_animation.setEndValue(0)
        try:
            self.panel_animation.finished.disconnect()
        except RuntimeError:
            pass
        self.panel_animation.finished.connect(self._finish_close_right_panel)
        self.panel_animation.start()

    def _finish_close_right_panel(self):
        self.right_panel.hide()
        self.right_panel.setMaximumWidth(16777215)
        if hasattr(self, "btn_reopen_panel"):
            self.btn_reopen_panel.setText("▶  PANNEAU")
        try:
            self.panel_animation.finished.disconnect(self._finish_close_right_panel)
        except RuntimeError:
            pass

    def _render_codex_results(self, data: dict, changed: list[str]):
        stats = data.get("stats", {}) if isinstance(data, dict) else {}
        diffs = data.get("diffs", {}) if isinstance(data, dict) else {}

        if not changed:
            self.results_summary.setText("Aucun fichier modifié.")
            self.diff_view.setPlainText("")
            return

        total_added = 0
        total_removed = 0
        blocks = []

        for rel in changed:
            if rel.lower() == "readme.md" and rel not in diffs:
                continue

            file_stats = stats.get(rel, {})
            added = int(file_stats.get("added", 0) or 0)
            removed = int(file_stats.get("deleted", 0) or 0)
            modified = int(file_stats.get("modified", 0) or 0)
            total_added += added
            total_removed += removed

            blocks.append(
                f"FILE {rel}    +{added}  -{removed}  ~{modified}"
            )

            raw_diff = str(diffs.get(rel, "") or "")
            if raw_diff:
                # Affiche uniquement les en-têtes et les vraies lignes + / -.
                for line in raw_diff.splitlines():
                    if (
                        line.startswith("+++ ")
                        or line.startswith("--- ")
                        or line.startswith("@@")
                        or (line.startswith("+") and not line.startswith("+++"))
                        or (line.startswith("-") and not line.startswith("---"))
                    ):
                        blocks.append(line)
            else:
                blocks.append("(diff détaillé non disponible)")
            blocks.append("")

        self.results_summary.setText(
            f"{len(changed)} fichier(s) touché(s)  •  "
            f"+{total_added} ligne(s)  •  -{total_removed} ligne(s)"
        )
        self.diff_view.setPlainText("\n".join(blocks).rstrip())
        self._show_right_panel(1)

    def _tool_button(self, text: str, callback):
        btn = QPushButton(text)
        btn.setObjectName("toolButton")
        btn.setMinimumHeight(48)
        btn.clicked.connect(callback)
        return btn

    def _apply_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background: #000000;
                color: #efffff;
                font-family: "Ink Free", "Segoe Print", "Comic Sans MS";
                font-size: 14px;
            }

            #panelTabButton {
                background: #050505;
                color: #baff64;
                border: 1px solid #00d9cc;
                border-radius: 6px;
                padding: 6px 10px;
                font-weight: 900;
            }

            #panelTabButton[active="true"] {
                background: #102000;
                color: #ffffff;
                border: 2px solid #39ff14;
            }

            #panelTabButton:hover {
                border-color: #39ff14;
                color: #ffffff;
            }

            #reopenPanelButton {
                background: #020202;
                color: #8eff55;
                border: 1px solid #39ff14;
                border-radius: 6px;
                padding: 5px 9px;
                font-weight: 900;
            }

            #reopenPanelButton:hover {
                background: #102000;
                color: #ffffff;
                border-color: #00efff;
            }

            #panelCloseButton {
                background: #170000;
                color: #ff6666;
                border: 1px solid #ff5252;
                border-radius: 6px;
                font-weight: 900;
            }

            #resultsSummary {
                background: #030303;
                color: #ffb347;
                border: 1px solid #ff8200;
                border-radius: 6px;
                padding: 7px;
                font-weight: 800;
            }

            #diffView {
                background: #000000;
                color: #dff;
                border: 1px solid #00d9cc;
                border-radius: 7px;
                padding: 7px;
                font-family: "Cascadia Code", "Consolas", monospace;
                font-size: 12px;
            }

            #enginePanel {
                background: #000000;
                border: 1px solid #00d9cc;
                border-radius: 8px;
            }

            #enginePanel {
                background: #000000;
                border: 2px solid #39ff14;
                border-radius: 10px;
            }

            #reflexionAvatar {
                background: #000000;
                color: #ff8a00;
                border: 2px solid #ff8a00;
                border-radius: 10px;
                padding: 2px;
                font-size: 34px;
                font-weight: 900;
            }

            #engineDetail {
                color: #dfffaa;
                font-size: 11px;
                font-weight: 700;
            }

            #engineStatus {
                color: #ff9d21;
                font-weight: 900;
                font-size: 12px;
            }

            #engineFlow {
                color: #8eff55;
                font-size: 11px;
                font-weight: 700;
            }

            #engineProgress {
                background: #050505;
                border: 1px solid #00d9cc;
                border-radius: 5px;
                min-height: 10px;
                max-height: 10px;
            }

            #engineProgress::chunk {
                background: #39ff14;
                border-radius: 4px;
            }


            #topStatus {
                background: #000000;
                color: #a9ff5a;
                border: 1px solid #00d9cc;
                border-radius: 7px;
                padding: 5px 10px;
                font-family: "Ink Free", "Segoe Print", "Comic Sans MS";
                font-size: 12px;
                font-weight: 700;
            }

            #langBadge {
                background: #000000;
                color: #baff64;
                border: 1px solid #39ff14;
                border-radius: 7px;
                padding: 5px 7px;
                font-family: "Ink Free", "Segoe Print", "Comic Sans MS";
                font-size: 12px;
                font-weight: 900;
            }

            #header {
                background: #000000;
                border: 1px solid #00e6d2;
                border-top: 2px solid #ff8200;
                border-radius: 10px;
            }

            #panel, #commandBar {
                background: #000000;
                border: 1px solid #00d9cc;
                border-radius: 9px;
            }

            #brand {
                color: #f4ffff;
                font-size: 30px;
                font-weight: 900;
                letter-spacing: 1px;
            }

            #subtitle {
                color: #6dffc8;
                font-size: 16px;
                font-weight: 600;
            }

            #headerNav {
                color: #d6eeee;
                font-size: 13px;
                letter-spacing: 0.5px;
            }

            #ollama {
                color: #39ff14;
                font-family: "Ink Free", "Segoe Print", "Comic Sans MS";
                font-weight: 800;
                padding: 8px 12px;
                border: 1px solid #00d9cc;
                border-radius: 8px;
                background: #000000;
            }

            #sectionTitle {
                color: #8eff55;
                font-weight: 900;
                font-size: 15px;
                letter-spacing: 0.5px;
            }

            #tabTitle {
                color: #a9ff5a;
                background: #000000;
                border: 1px solid #00d9cc;
                border-bottom: 2px solid #39ff14;
                border-radius: 7px;
                padding: 7px 11px;
                font-family: "Ink Free", "Segoe Print", "Comic Sans MS";
                font-weight: 800;
            }

            #projectTree {
                background: #000000;
                border: 1px solid #00d9cc;
                border-radius: 7px;
                color: #f0ffff;
                outline: 0;
            }

            #projectTree::item {
                min-height: 26px;
                padding-left: 4px;
            }

            #projectTree::item:hover {
                background: #0a2019;
                color: #8eff55;
            }

            #projectTree::item:selected {
                background: #234b0b;
                color: #dfff9a;
                border: 1px solid #39ff14;
            }

            #newProjectButton {
                background: #061607;
                color: #8eff55;
                border: 2px solid #39ff14;
                border-radius: 8px;
                padding: 8px;
                font-weight: 900;
            }

            #newProjectButton:hover {
                background: #103b10;
            }

            #openProjectButton {
                background: #231300;
                color: #ffb347;
                border: 2px solid #ff8200;
                border-radius: 8px;
                padding: 8px;
                font-weight: 900;
            }

            #openProjectButton:hover {
                background: #4a2500;
            }

            #infoCard {
                min-height: 145px;
                max-height: 185px;
                background: #000000;
                border: 1px solid #00d9cc;
                border-radius: 8px;
            }

            #cardLogo {
                color: #7dff38;
                font-size: 54px;
                font-weight: 900;
            }

            #cardTitle {
                color: #f2ffff;
                font-size: 20px;
                font-weight: 900;
                letter-spacing: 1px;
            }

            #cardText {
                color: #d5eeee;
                font-size: 12px;
                letter-spacing: 2px;
            }

            #cardFooter {
                color: #b8ff55;
                font-size: 11px;
                font-weight: 700;
            }

            #versionLabel {
                color: #8eff55;
                font-family: "Ink Free", "Segoe Print", "Comic Sans MS";
                font-weight: 900;
                font-size: 12px;
            }

            #editor {
                background: #000000;
                color: #efffff;
                font-family: "Ink Free", "Segoe Print", "Comic Sans MS";
                font-size: 15px;
                font-weight: 600;
                border: 1px solid #00d9cc;
                border-radius: 7px;
                selection-background-color: #214f25;
                selection-color: #ffffff;
                padding: 8px;
            }

            #editor:focus {
                border: 1px solid #39ff14;
            }

            #output {
                background: #000000;
                color: #dbffff;
                font-family: "Ink Free", "Segoe Print", "Comic Sans MS";
                font-size: 13px;
                font-weight: 600;
                border: 1px solid #00d9cc;
                border-radius: 7px;
                font-family: "Ink Free", "Segoe Print", "Comic Sans MS";
                font-size: 12px;
            }

            #toolButton {
                background: #120700;
                color: #ffad24;
                border: 2px solid #ff7a00;
                border-radius: 8px;
                font-size: 15px;
                font-weight: 900;
                text-align: left;
                padding: 9px 12px;
            }

            #toolButton:hover {
                background: #4b2400;
                color: #ffe0a0;
                border-color: #ffb000;
            }

            #toolButton:pressed {
                background: #6f3100;
                color: white;
            }

            #clearButton {
                background: #000000;
                color: #eaffff;
                border: 1px solid #00d9cc;
                border-radius: 7px;
                padding: 7px;
                font-weight: 700;
            }

            QPushButton {
                background: #000000;
                color: #efffff;
                border: 1px solid #00bfb5;
                border-radius: 7px;
                padding: 7px 10px;
                font-weight: 700;
            }

            QPushButton:hover {
                color: #8eff55;
                border-color: #39ff14;
            }

            #sendButton {
                min-width: 180px;
                min-height: 55px;
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ff7900,
                    stop:1 #ffb000
                );
                color: #120800;
                border: 2px solid #ffc246;
                border-radius: 8px;
                font-size: 16px;
                font-weight: 900;
            }

            #sendButton:hover {
                background: #ffc02a;
                border-color: #fff18a;
            }

            #prompt, QComboBox {
                background: #000000;
                color: #ffffff;
                border: 2px solid #39ff14;
                border-radius: 7px;
                padding: 8px 10px;
                min-height: 28px;
                font-family: "Ink Free", "Segoe Print", "Comic Sans MS";
            }

            #prompt:focus, QComboBox:focus {
                border-color: #00efff;
            }

            #muted {
                color: #76a9a9;
                font-size: 10px;
            }

            QSplitter::handle {
                background: #07302e;
                width: 4px;
            }

            QSplitter::handle:hover {
                background: #39ff14;
            }

            QScrollBar:vertical {
                background: #031010;
                width: 11px;
                border: 0;
            }

            QScrollBar::handle:vertical {
                background: #39ff14;
                min-height: 28px;
                border-radius: 5px;
            }

            QScrollBar::handle:vertical:hover {
                background: #00efff;
            }

            QToolTip {
                background: #020606;
                color: #39ff14;
                border: 1px solid #39ff14;
            }
        """)

    def _set_engine_stage(self, stage: str, detail: str = "", progress: int = 0):
        stage = stage.upper().strip()
        text = f"MOTEUR DE RÉFLEXION  •  {stage}"
        self.engine_stage_text = text

        if hasattr(self, "engine_status"):
            prefix = self.thinking_frames[self.thinking_index] if self.thinking_timer.isActive() else "●"
            self.engine_status.setText(f"{prefix} {text}")

        if hasattr(self, "engine_detail"):
            self.engine_detail.setText(detail or "Traitement en cours…")

        if hasattr(self, "engine_progress"):
            self.engine_progress.setValue(max(0, min(100, progress)))

    def _start_thinking_animation(self):
        self.thinking_index = 0
        if not self.thinking_timer.isActive():
            self.thinking_timer.start()
        self._animate_thinking()

    def _stop_thinking_animation(self):
        if self.thinking_timer.isActive():
            self.thinking_timer.stop()
        if hasattr(self, "engine_status"):
            self.engine_status.setText(f"● {self.engine_stage_text}")

    def _animate_thinking(self):
        if not hasattr(self, "engine_status"):
            return
        frame = self.thinking_frames[self.thinking_index % len(self.thinking_frames)]
        self.thinking_index = (self.thinking_index + 1) % len(self.thinking_frames)
        self.engine_status.setText(f"{frame} {self.engine_stage_text}")

    def _update_engine_from_status(self, message: str):
        low = message.lower()
        if any(word in low for word in ("plan", "analyse", "analy")):
            self._set_engine_stage("PLAN", "préparation des actions", 25)
        elif any(word in low for word in ("écrit", "write", "génér", "generate", "patch", "direct")):
            self._set_engine_stage("ÉCRITURE", "modification des fichiers", 65)
        elif any(word in low for word in ("valid", "test", "compile", "repair", "corrig")):
            self._set_engine_stage("VALIDATION", "contrôle et correction", 82)
        elif any(word in low for word in ("apply", "appli", "exécut", "execute", "action")):
            self._set_engine_stage("EXÉCUTION", "application du plan", 45)

    def _update_readme_summary(self, request: str, changed: list[str], result_message: str):
        readme = self.project_root / "README.md"
        try:
            previous = readme.read_text(encoding="utf-8", errors="replace") if readme.exists() else f"# {self.project_root.name}\n"
            start = "<!-- TI-LEX-CODEX-SUMMARY:START -->"
            end = "<!-- TI-LEX-CODEX-SUMMARY:END -->"
            files = ", ".join(changed) if changed else "aucun fichier signalé"
            block = (
                f"\n{start}\n"
                f"## Dernière action TI-LEX CODEX\n\n"
                f"- **Commande :** {request}\n"
                f"- **Résultat :** {result_message}\n"
                f"- **Fichiers :** {files}\n"
                f"- **Mise à jour :** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"{end}\n"
            )
            if start in previous and end in previous:
                before = previous.split(start, 1)[0].rstrip()
                after = previous.split(end, 1)[1].lstrip()
                content = before + block + ("\n" + after if after else "")
            else:
                content = previous.rstrip() + "\n" + block
            readme.write_text(content, encoding="utf-8")
            return "README.md"
        except Exception as exc:
            self._log(f"Résumé README non mis à jour : {exc}", "WARN")
            return None

    def _log(self, message: str, kind: str = "INFO"):
        self.output.appendPlainText(f"[{kind}]  {message}")

    def _load_project(self, root: Path):
        self.project_root = root.resolve()
        self.project_label.setText(str(self.project_root))
        self.tree.clear()

        root_item = QTreeWidgetItem([f"📁 {self.project_root.name}"])
        root_item.setData(0, Qt.UserRole, str(self.project_root))
        self.tree.addTopLevelItem(root_item)

        self._populate_tree(root_item, self.project_root, depth=0)
        root_item.setExpanded(True)
        self._log(f"Projet chargé : {self.project_root}")

    def _populate_tree(self, parent_item: QTreeWidgetItem, folder: Path, depth: int):
        if depth > 6:
            return

        try:
            entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return

        for path in entries:
            if path.name in IGNORE_DIRS:
                continue
            if path.name.startswith(".") and path.name not in {".env.example", ".gitignore"}:
                continue

            if path.is_dir():
                item = QTreeWidgetItem([f"📁 {path.name}"])
                item.setData(0, Qt.UserRole, str(path))
                item.setData(0, Qt.UserRole + 1, "dir")
                parent_item.addChild(item)
                self._populate_tree(item, path, depth + 1)
            elif path.suffix.lower() in TEXT_EXTENSIONS or path.name in {"README.md", ".gitignore"}:
                icon_map = {
                    ".py": "🐍", ".js": "🟨", ".jsx": "🟨", ".ts": "🔷", ".tsx": "🔷",
                    ".html": "🌐", ".htm": "🌐", ".css": "🎨", ".scss": "🎨",
                    ".json": "🧩", ".md": "Ⓜ", ".ps1": "💠", ".sh": "🐚",
                    ".c": "©", ".cpp": "C++", ".java": "☕", ".go": "🔹", ".rs": "⚙",
                }
                icon = icon_map.get(path.suffix.lower(), "📄")
                item = QTreeWidgetItem([f"{icon} {path.name}"])
                item.setData(0, Qt.UserRole, str(path))
                item.setData(0, Qt.UserRole + 1, "file")
                parent_item.addChild(item)

    def _open_tree_item(self, item: QTreeWidgetItem, _column: int):
        if item.data(0, Qt.UserRole + 1) != "file":
            item.setExpanded(not item.isExpanded())
            return
        path = Path(item.data(0, Qt.UserRole))
        self.open_file(path)

    def _update_language_badge(self, path: Path):
        ext = path.suffix.lower()
        language_map = {
            ".py": ("🐍", "PYTHON"),
            ".js": ("🟨", "JAVASCRIPT"),
            ".jsx": ("🟨", "JAVASCRIPT / JSX"),
            ".ts": ("🔷", "TYPESCRIPT"),
            ".tsx": ("🔷", "TYPESCRIPT / TSX"),
            ".html": ("🌐", "HTML"),
            ".htm": ("🌐", "HTML"),
            ".css": ("🎨", "CSS"),
            ".scss": ("🎨", "SCSS"),
            ".json": ("🧩", "JSON"),
            ".md": ("Ⓜ", "MARKDOWN"),
            ".ps1": ("💠", "POWERSHELL"),
            ".sh": ("🐚", "SHELL"),
            ".c": ("©", "C"),
            ".cpp": ("C++", "C++"),
            ".java": ("☕", "JAVA"),
            ".go": ("🔹", "GO"),
            ".rs": ("⚙", "RUST"),
        }
        icon, language = language_map.get(ext, ("📄", ext.lstrip(".").upper() or "FICHIER"))
        self.lang_badge.setText(f"{icon}  {language}  •  {path.name}")

    def _animate_generated_file(self, path: Path):
        """Affiche un fichier généré avec un effet d'écriture en temps réel."""
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            self._log(f"Lecture du fichier généré impossible : {exc}", "WARN")
            self.open_file(path)
            return

        if self.typewriter_timer.isActive():
            self.typewriter_timer.stop()

        self.current_file = path
        self._update_language_badge(path)
        try:
            rel = path.relative_to(self.project_root)
        except ValueError:
            rel = path

        self.file_title.setText(f"✍  ÉCRITURE EN TEMPS RÉEL  •  {rel}")
        self._set_engine_stage("ÉCRITURE", f"{rel}", 70)

        self.editor.setUpdatesEnabled(False)
        self.editor.clear()
        self.editor.setUpdatesEnabled(True)

        self.typewriter_text = content
        self.typewriter_index = 0

        # Les gros fichiers restent fluides sans prendre plusieurs minutes.
        length = len(content)
        if length > 30000:
            self.typewriter_chunk = 48
        elif length > 15000:
            self.typewriter_chunk = 24
        elif length > 6000:
            self.typewriter_chunk = 10
        elif length > 2500:
            self.typewriter_chunk = 5
        else:
            self.typewriter_chunk = 2

        self.editor.setReadOnly(True)
        self.typewriter_timer.start()

    def _typewriter_step(self):
        if self.typewriter_index >= len(self.typewriter_text):
            self.typewriter_timer.stop()
            self.editor.setReadOnly(False)
            if self.current_file:
                try:
                    rel = self.current_file.relative_to(self.project_root)
                except ValueError:
                    rel = self.current_file
                self.file_title.setText(f"📄 {rel}")
                self._set_engine_stage("VALIDATION", "écriture terminée", 85)
                self._log(f"Écriture en temps réel terminée : {rel}", "SUCCESS")
            return

        end = min(
            self.typewriter_index + self.typewriter_chunk,
            len(self.typewriter_text),
        )
        chunk = self.typewriter_text[self.typewriter_index:end]
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(chunk)
        self.editor.setTextCursor(cursor)
        self.editor.ensureCursorVisible()
        self.typewriter_index = end

    def open_file(self, path: Path):
        if self.typewriter_timer.isActive():
            self.typewriter_timer.stop()
            self.editor.setReadOnly(False)

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return

        self.current_file = path
        self.editor.setPlainText(content)
        self._update_language_badge(path)
        try:
            rel = path.relative_to(self.project_root)
        except ValueError:
            rel = path
        self.file_title.setText(f"📄 {rel}")
        self._log(f"Fichier ouvert : {rel}")

    def save_current(self):
        if self.typewriter_timer.isActive():
            self._log("Attends la fin de l'écriture en temps réel avant de sauvegarder.", "WARN")
            return
        if not self.current_file:
            QMessageBox.information(self, "TI-LEX CODEX", "Aucun fichier ouvert.")
            return
        try:
            self.current_file.write_text(self.editor.toPlainText(), encoding="utf-8")
            self._log(f"Sauvegardé : {self.current_file.name}", "SUCCESS")
        except OSError as exc:
            QMessageBox.critical(self, "Erreur sauvegarde", str(exc))

    def new_project(self):
        try:
            parent = QFileDialog.getExistingDirectory(
                self,
                "Choisir où créer le nouveau projet",
                str(self.project_root.parent),
            )
            if not parent:
                return

            name, ok = QInputDialog.getText(
                self,
                "Nouveau projet",
                "Nom du projet :",
            )
            if not ok:
                return

            name = name.strip()
            if not name:
                QMessageBox.warning(self, "Nouveau projet", "Le nom du projet est vide.")
                return

            forbidden = '<>:"/\\|?*'
            if name in {".", ".."} or any(ch in name for ch in forbidden) or any(ord(ch) < 32 for ch in name):
                QMessageBox.warning(
                    self,
                    "Nom invalide",
                    "Utilise un nom simple sans caractères < > : \" / \\ | ? *",
                )
                return

            project = (Path(parent) / name).resolve()
            if project.exists():
                answer = QMessageBox.question(
                    self,
                    "Projet existant",
                    f"Le dossier existe déjà :\n{project}\n\nVeux-tu l'ouvrir ?",
                    QMessageBox.Yes | QMessageBox.No,
                )
                if answer == QMessageBox.Yes and project.is_dir():
                    self.current_file = None
                    self.editor.clear()
                    self.file_title.setText("📄 Aucun fichier ouvert")
                    self.lang_badge.setText("📄  AUCUN FICHIER")
                    self._load_project(project)
                return

            project.mkdir(parents=False, exist_ok=False)
            readme = project / "README.md"
            readme.write_text(
                f"# {name}\n\nProjet créé avec TI-LEX CODEX.\n",
                encoding="utf-8",
            )

            self.current_file = None
            self.editor.clear()
            self.file_title.setText("📄 Aucun fichier ouvert")
            self.lang_badge.setText("📄  AUCUN FICHIER")
            self._load_project(project)
            self._log(f"Nouveau projet créé : {project}", "SUCCESS")
            QMessageBox.information(
                self,
                "Projet créé",
                f"Le projet {name} a été créé et ouvert.",
            )
        except Exception as exc:
            self._record_gui_exception(exc, "new_project")
            self._log(f"Création projet : {exc}", "ERROR")
            QMessageBox.critical(
                self,
                "Erreur création projet",
                f"Impossible de créer le projet.\n\n{type(exc).__name__}: {exc}",
            )

    def open_project(self):
        selected = QFileDialog.getExistingDirectory(self, "Ouvrir un projet", str(self.project_root))
        if selected:
            self.current_file = None
            self.editor.clear()
            self.file_title.setText("📄 Aucun fichier ouvert")
            self.lang_badge.setText("📄  AUCUN FICHIER")
            self._load_project(Path(selected))

    def run_current(self):
        if not self.current_file:
            QMessageBox.information(self, "TI-LEX CODEX", "Ouvre un fichier avant de lancer.")
            return
        self.save_current()
        if self.current_file.suffix.lower() != ".py":
            QMessageBox.information(self, "TI-LEX CODEX", "La V1 lance directement les fichiers Python.")
            return
        try:
            subprocess.Popen(
                [sys.executable, str(self.current_file)],
                cwd=str(self.current_file.parent),
            )
            self._log(f"Lancement : {self.current_file.name}", "SUCCESS")
        except OSError as exc:
            QMessageBox.critical(self, "Erreur lancement", str(exc))

    def test_current(self):
        if not self.current_file:
            QMessageBox.information(self, "TI-LEX CODEX", "Aucun fichier ouvert.")
            return
        self.save_current()
        if self.current_file.suffix.lower() != ".py":
            self._log("Test automatique V1 disponible pour Python.", "WARN")
            return
        try:
            py_compile.compile(str(self.current_file), doraise=True)
            self._log(f"Validation Python OK : {self.current_file.name}", "SUCCESS")
        except py_compile.PyCompileError as exc:
            self._log(str(exc), "ERROR")
            QMessageBox.critical(self, "Erreur Python", str(exc))

    def build_current(self):
        self.test_current()
        self._log("Build V1 terminé : validation du fichier courant.", "INFO")

    def create_zip(self):
        target = self.project_root.parent / f"{self.project_root.name}-backup"
        try:
            archive = shutil.make_archive(str(target), "zip", root_dir=str(self.project_root))
            self._log(f"ZIP créé : {archive}", "SUCCESS")
            QMessageBox.information(self, "ZIP créé", archive)
        except OSError as exc:
            QMessageBox.critical(self, "Erreur ZIP", str(exc))

    def _refresh_ollama_status(self):
        try:
            response = requests.get("http://127.0.0.1:11434/api/tags", timeout=2)
            response.raise_for_status()
            models = [m.get("name", "") for m in response.json().get("models", [])]
            model = self.config.get("model", "qwen2.5:7b")
            if model not in models and models:
                model = models[0]
            self.ollama_label.setText(f"● Ollama : connecté\nModèle : {model}")
            self._log(f"Ollama connecté • modèle : {model}", "SUCCESS")
        except Exception:
            self.ollama_label.setText("● Ollama : hors ligne")
            self.ollama_label.setStyleSheet("color:#ff4d4d;")
            self._log("Ollama non détecté sur 127.0.0.1:11434", "WARN")

    def send_codex(self):
        try:
            # Une seule commande à la fois : évite les collisions de QThread.
            if self.worker_thread is not None and self.worker_thread.isRunning():
                self._log("Une commande CODEX est déjà en cours.", "WARN")
                return

            request = self.prompt.text().strip()
            if not request:
                return

            self.last_codex_request = request
            self._set_engine_stage("ANALYSE", "compréhension de la commande", 10)
            self._start_thinking_animation()

            mode = self.mode_combo.currentText()
            if mode == "PRO":
                request = "/pro " + request
            elif mode == "DIRECT":
                request = "/fast " + request

            self.prompt.clear()
            self.prompt.setEnabled(False)
            self._set_tools_enabled(False)
            self._log(f"Commande : {request}")
            self._log("CODEX travaille…", "INFO")

            thread = QThread(self)
            worker = CodexWorker(
                self.project_root,
                request,
                self.config.get("model", "qwen2.5:7b"),
            )
            worker.moveToThread(thread)

            thread.started.connect(worker.run)
            worker.status.connect(self._safe_worker_status)
            worker.finished.connect(self._codex_finished)
            worker.failed.connect(self._codex_failed)
            worker.finished.connect(thread.quit)
            worker.failed.connect(thread.quit)
            thread.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater)
            thread.finished.connect(self._worker_cleanup)

            self.worker_thread = thread
            self.worker = worker
            thread.start()
        except BaseException as exc:
            self._stop_thinking_animation()
            self._record_gui_exception(exc, "send_codex")
            self.prompt.setEnabled(True)
            self._set_tools_enabled(True)
            self._log(f"{type(exc).__name__}: {exc}", "ERROR")
            QMessageBox.critical(
                self,
                "Erreur CODEX",
                f"La commande a échoué, mais l'interface reste ouverte.\n\n{type(exc).__name__}: {exc}",
            )

    def _safe_worker_status(self, message: str):
        try:
            self._update_engine_from_status(str(message))
            self._log(str(message), "CODEX")
        except Exception as exc:
            self._record_gui_exception(exc, "worker_status")

    def _worker_cleanup(self):
        self.worker = None
        self.worker_thread = None

    def _record_gui_exception(self, exc: BaseException, where: str = "GUI"):
        try:
            state_dir = self.project_root / ".tilex"
            state_dir.mkdir(parents=True, exist_ok=True)
            report = (
                f"TI-LEX CODEX GUI ERROR\n"
                f"ZONE: {where}\n"
                f"TYPE: {type(exc).__name__}\n"
                f"MESSAGE: {exc}\n\n"
                f"{traceback.format_exc()}"
            )
            (state_dir / "gui_crash.log").write_text(report, encoding="utf-8")
        except Exception:
            pass

    def _codex_finished(self, payload):
        try:
            result, run_data = payload
            outputs = run_data.get("outputs", {}) if isinstance(run_data, dict) else {}
            self.prompt.setEnabled(True)
            self._set_tools_enabled(True)

            if not result.ok:
                self._stop_thinking_animation()
                self._log(result.message, "ERROR")
                QMessageBox.critical(self, "Erreur CODEX", result.message)
                self.prompt.setFocus()
                return

            self._log(result.message, "SUCCESS")
            changed = list(result.changed or [])
            self._set_engine_stage("README", "mise à jour du résumé", 92)
            readme_changed = self._update_readme_summary(
                self.last_codex_request,
                changed,
                result.message,
            )
            if readme_changed and readme_changed not in changed:
                changed.append(readme_changed)

            self._render_codex_results(run_data, changed)

            # Le rafraîchissement du projet ne doit jamais fermer l'interface.
            try:
                self._load_project(self.project_root)
            except Exception as exc:
                self._record_gui_exception(exc, "refresh_project")
                self._log(f"Rafraîchissement projet: {exc}", "WARN")

            if changed:
                first = (self.project_root / changed[0]).resolve()
                if first.is_file():
                    try:
                        self._animate_generated_file(first)
                    except Exception as exc:
                        self._record_gui_exception(exc, "open_changed_file")
                        self._log(f"Ouverture fichier modifié: {exc}", "WARN")

            self._set_engine_stage("TERMINÉ", "commande complétée", 100)
            self._stop_thinking_animation()
            self.prompt.setFocus()
        except BaseException as exc:
            self._stop_thinking_animation()
            self._record_gui_exception(exc, "_codex_finished")
            self.prompt.setEnabled(True)
            self._set_tools_enabled(True)
            self._log(f"{type(exc).__name__}: {exc}", "ERROR")
            QMessageBox.critical(
                self,
                "Erreur interface",
                f"Le moteur a terminé, mais l'affichage a rencontré une erreur.\n"
                f"L'interface reste ouverte.\n\n{type(exc).__name__}: {exc}",
            )

    def _codex_failed(self, message: str):
        try:
            self._stop_thinking_animation()
            self.prompt.setEnabled(True)
            self._set_tools_enabled(True)
            self._set_engine_stage("ERREUR", str(message), 0)
            self._log(message, "ERROR")
            QMessageBox.critical(
                self,
                "Erreur CODEX",
                "La commande a échoué, mais l'interface reste ouverte.\n\n" + str(message),
            )
            self.prompt.setFocus()
        except BaseException as exc:
            self._record_gui_exception(exc, "_codex_failed")

    def _set_tools_enabled(self, enabled: bool):
        for btn in (
            self.btn_run, self.btn_save, self.btn_test, self.btn_build,
            self.btn_new, self.btn_open, self.btn_zip
        ):
            btn.setEnabled(enabled)


def main():
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    app = QApplication(sys.argv)
    app.setApplicationName("TI-LEX CODEX")
    app.setOrganizationName("Marceau")
    window = TiLexCodexWindow()

    def gui_exception_hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        try:
            state_dir = window.project_root / ".tilex"
            state_dir.mkdir(parents=True, exist_ok=True)
            report = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            (state_dir / "gui_crash.log").write_text(report, encoding="utf-8")
            window._log(f"{exc_type.__name__}: {exc_value}", "ERROR")
            QMessageBox.critical(
                window,
                "Erreur interface",
                "Une erreur a été interceptée. TI-LEX CODEX reste ouvert.\n\n"
                f"{exc_type.__name__}: {exc_value}",
            )
        except Exception:
            sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = gui_exception_hook
    window.show()
    return app.exec()


if __name__ == "__main__":
    main()
