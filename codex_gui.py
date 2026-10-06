from __future__ import annotations

import os
import py_compile
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

import requests
from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QSyntaxHighlighter, QTextCharFormat
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
    QSizePolicy,
    QSplitter,
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
            self.finished.emit((result, dict(engine.last_outputs)))
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

        # ===== MAIN AREA =====
        main_splitter = QSplitter(Qt.Horizontal)
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
        left_layout.addWidget(self.tree, 1)

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

        # Decorative TI-LEX card, matching the approved mockup
        info_card = QFrame(objectName="infoCard")
        info_layout = QVBoxLayout(info_card)
        info_layout.setContentsMargins(14, 14, 14, 14)
        info_layout.setSpacing(8)

        logo = QLabel("▲")
        logo.setObjectName("cardLogo")
        logo.setAlignment(Qt.AlignCenter)

        card_title = QLabel("IA CODEX\nMARCEAU")
        card_title.setObjectName("cardTitle")
        card_title.setAlignment(Qt.AlignCenter)

        card_text = QLabel("COMPRENDRE\nDÉVELOPPER\nOPTIMISER\nAUTOMATISER")
        card_text.setObjectName("cardText")
        card_text.setAlignment(Qt.AlignCenter)

        card_footer = QLabel("Noir + vert lime + orange")
        card_footer.setObjectName("cardFooter")
        card_footer.setAlignment(Qt.AlignCenter)

        version = QLabel("///                                      v1.0.0")
        version.setObjectName("versionLabel")

        info_layout.addWidget(logo)
        info_layout.addWidget(card_title)
        info_layout.addWidget(card_text)
        info_layout.addStretch()
        info_layout.addWidget(card_footer)
        info_layout.addWidget(version)

        left_layout.addWidget(info_card)

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
        pretty_font = QFont("Segoe Print")
        pretty_font.setPointSize(14)
        pretty_font.setWeight(QFont.Medium)
        self.editor.setFont(pretty_font)
        self.highlighter = PythonHighlighter(self.editor.document())
        center_layout.addWidget(self.editor, 1)

        # RIGHT
        right = QFrame(objectName="panel")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 8, 8, 8)
        right_layout.setSpacing(7)

        tools_title = QLabel("🔧  OUTILS")
        tools_title.setObjectName("sectionTitle")
        right_layout.addWidget(tools_title)

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
            right_layout.addWidget(btn)

        status_title = QLabel("🖥  STATUT / SORTIE")
        status_title.setObjectName("sectionTitle")
        right_layout.addWidget(status_title)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setObjectName("output")
        self.output.setMaximumBlockCount(1000)
        self.output.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right_layout.addWidget(self.output, 1)

        clear_btn = QPushButton("🗑  Effacer")
        clear_btn.setObjectName("clearButton")
        clear_btn.clicked.connect(self.output.clear)
        right_layout.addWidget(clear_btn)

        main_splitter.addWidget(left)
        main_splitter.addWidget(center)
        main_splitter.addWidget(right)
        main_splitter.setStretchFactor(0, 2)
        main_splitter.setStretchFactor(1, 7)
        main_splitter.setStretchFactor(2, 3)
        main_splitter.setSizes([330, 930, 390])

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

        self.btn_save.setShortcut(QKeySequence("Ctrl+S"))
        self.btn_run.setShortcut(QKeySequence("F5"))
        self.btn_test.setShortcut(QKeySequence("Ctrl+T"))
        self.btn_build.setShortcut(QKeySequence("Ctrl+B"))
        self.btn_open.setShortcut(QKeySequence("Ctrl+O"))

    def _tool_button(self, text: str, callback):
        btn = QPushButton(text)
        btn.setObjectName("toolButton")
        btn.setMinimumHeight(48)
        btn.clicked.connect(callback)
        return btn

    def _apply_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background: #010404;
                color: #efffff;
                font-family: "Segoe Print";
                font-size: 14px;
            }

            #header {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #041010,
                    stop:0.50 #020707,
                    stop:1 #061008
                );
                border: 1px solid #00e6d2;
                border-top: 2px solid #ff8200;
                border-radius: 10px;
            }

            #panel, #commandBar {
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 #061011,
                    stop:1 #020606
                );
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
                font-family: "Segoe Print";
                font-weight: 800;
                padding: 8px 12px;
                border: 1px solid #00d9cc;
                border-radius: 8px;
                background: #03100b;
            }

            #sectionTitle {
                color: #8eff55;
                font-weight: 900;
                font-size: 15px;
                letter-spacing: 0.5px;
            }

            #tabTitle {
                color: #a9ff5a;
                background: #071010;
                border: 1px solid #00d9cc;
                border-bottom: 2px solid #39ff14;
                border-radius: 7px;
                padding: 7px 11px;
                font-family: "Segoe Print";
                font-weight: 800;
            }

            #projectTree {
                background: #010606;
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
                min-height: 240px;
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:1,
                    stop:0 #030a08,
                    stop:1 #07180d
                );
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
                font-size: 12px;
                font-weight: 700;
            }

            #versionLabel {
                color: #8eff55;
                font-family: "Segoe Print";
                font-weight: 900;
                font-size: 12px;
            }

            #editor {
                background: #010507;
                color: #efffff;
                font-family: "Segoe Print";
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
                background: #010507;
                color: #dbffff;
                font-family: "Segoe Print";
                font-size: 13px;
                font-weight: 600;
                border: 1px solid #00d9cc;
                border-radius: 7px;
                font-family: "Segoe Print";
                font-size: 12px;
            }

            #toolButton {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #351700,
                    stop:1 #1d0d00
                );
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
                background: #061111;
                color: #eaffff;
                border: 1px solid #00d9cc;
                border-radius: 7px;
                padding: 7px;
                font-weight: 700;
            }

            QPushButton {
                background: #061111;
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
                background: #010505;
                color: #ffffff;
                border: 2px solid #39ff14;
                border-radius: 7px;
                padding: 8px 10px;
                min-height: 28px;
                font-family: "Segoe Print";
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
                icon = "🐍" if path.suffix.lower() == ".py" else "📄"
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

    def open_file(self, path: Path):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return

        self.current_file = path
        self.editor.setPlainText(content)
        try:
            rel = path.relative_to(self.project_root)
        except ValueError:
            rel = path
        self.file_title.setText(f"📄 {rel}")
        self._log(f"Fichier ouvert : {rel}")

    def save_current(self):
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
            result, outputs = payload
            self.prompt.setEnabled(True)
            self._set_tools_enabled(True)

            if not result.ok:
                self._log(result.message, "ERROR")
                QMessageBox.critical(self, "Erreur CODEX", result.message)
                self.prompt.setFocus()
                return

            self._log(result.message, "SUCCESS")
            changed = list(result.changed or [])

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
                        self.open_file(first)
                    except Exception as exc:
                        self._record_gui_exception(exc, "open_changed_file")
                        self._log(f"Ouverture fichier modifié: {exc}", "WARN")

            self.prompt.setFocus()
        except BaseException as exc:
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
            self.prompt.setEnabled(True)
            self._set_tools_enabled(True)
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
