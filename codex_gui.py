from __future__ import annotations

import os
import py_compile
import shutil
import subprocess
import sys
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

        keyword = fmt("#ff8a00", True)
        builtin = fmt("#00e5ff")
        string = fmt("#9cff57")
        comment = fmt("#6b7280")
        number = fmt("#ffd166")
        decorator = fmt("#ff4d8d")

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
        except Exception as exc:
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
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(8)

        header = QFrame(objectName="header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 10, 18, 10)

        brand = QLabel("◢  TI-LEX CODEX")
        brand.setObjectName("brand")
        subtitle = QLabel("IA Codex Marceau")
        subtitle.setObjectName("subtitle")

        brand_box = QVBoxLayout()
        brand_box.setSpacing(0)
        brand_box.addWidget(brand)
        brand_box.addWidget(subtitle)

        header_layout.addLayout(brand_box)
        header_layout.addStretch()

        self.ollama_label = QLabel("● Ollama : vérification…")
        self.ollama_label.setObjectName("ollama")
        header_layout.addWidget(self.ollama_label)

        root_layout.addWidget(header)

        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.setChildrenCollapsible(False)

        # LEFT: project explorer
        left = QFrame(objectName="panel")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_title = QLabel("📁  PROJET")
        left_title.setObjectName("sectionTitle")
        left_layout.addWidget(left_title)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setObjectName("projectTree")
        self.tree.itemDoubleClicked.connect(self._open_tree_item)
        left_layout.addWidget(self.tree, 1)

        open_project = QPushButton("📂  Ouvrir projet")
        open_project.clicked.connect(self.open_project)
        left_layout.addWidget(open_project)

        self.project_label = QLabel("")
        self.project_label.setWordWrap(True)
        self.project_label.setObjectName("muted")
        left_layout.addWidget(self.project_label)

        # CENTER: editor
        center = QFrame(objectName="panel")
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(8, 8, 8, 8)
        center_layout.setSpacing(6)

        self.file_title = QLabel("📄 Aucun fichier ouvert")
        self.file_title.setObjectName("tabTitle")
        center_layout.addWidget(self.file_title)

        self.editor = QPlainTextEdit()
        self.editor.setObjectName("editor")
        self.editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        mono = QFont("Cascadia Code")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(11)
        self.editor.setFont(mono)
        self.highlighter = PythonHighlighter(self.editor.document())
        center_layout.addWidget(self.editor, 1)

        # RIGHT: tools + output
        right = QFrame(objectName="panel")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(10, 10, 10, 10)
        right_layout.setSpacing(8)

        tools_title = QLabel("🔧  OUTILS")
        tools_title.setObjectName("sectionTitle")
        right_layout.addWidget(tools_title)

        self.btn_run = self._tool_button("▶  Lancer", self.run_current)
        self.btn_save = self._tool_button("💾  Sauvegarder", self.save_current)
        self.btn_test = self._tool_button("🧪  Tester", self.test_current)
        self.btn_build = self._tool_button("⬢  Build", self.build_current)
        self.btn_open = self._tool_button("📂  Ouvrir projet", self.open_project)
        self.btn_zip = self._tool_button("🗜  Créer ZIP", self.create_zip)

        for btn in (self.btn_run, self.btn_save, self.btn_test, self.btn_build, self.btn_open, self.btn_zip):
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
        clear_btn.clicked.connect(self.output.clear)
        right_layout.addWidget(clear_btn)

        main_splitter.addWidget(left)
        main_splitter.addWidget(center)
        main_splitter.addWidget(right)
        main_splitter.setStretchFactor(0, 2)
        main_splitter.setStretchFactor(1, 7)
        main_splitter.setStretchFactor(2, 3)
        main_splitter.setSizes([300, 900, 360])

        root_layout.addWidget(main_splitter, 1)

        # BOTTOM command bar
        bottom = QFrame(objectName="commandBar")
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(12, 8, 12, 8)

        prompt_box = QVBoxLayout()
        prompt_title = QLabel("💬  Commande Codex")
        prompt_title.setObjectName("sectionTitle")
        self.prompt = QLineEdit()
        self.prompt.setObjectName("prompt")
        self.prompt.setPlaceholderText("Ex: ajoute une fonction de validation dans config.py")
        self.prompt.returnPressed.connect(self.send_codex)
        prompt_box.addWidget(prompt_title)
        prompt_box.addWidget(self.prompt)

        send = QPushButton("➤  Envoyer")
        send.setObjectName("sendButton")
        send.clicked.connect(self.send_codex)

        mode_box = QVBoxLayout()
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
                background: #030707;
                color: #f5f7f8;
                font-family: "Segoe UI";
                font-size: 14px;
            }
            #header, #panel, #commandBar {
                background: #071010;
                border: 1px solid #163737;
                border-radius: 9px;
            }
            #brand {
                color: #ff8a00;
                font-size: 30px;
                font-weight: 800;
                letter-spacing: 1px;
            }
            #subtitle {
                color: #39ff14;
                font-size: 16px;
            }
            #ollama {
                color: #39ff14;
                font-family: "Cascadia Code";
                font-weight: 700;
                padding: 8px 12px;
                border: 1px solid #00a86b;
                border-radius: 8px;
                background: #04110d;
            }
            #sectionTitle {
                color: #9cff57;
                font-weight: 800;
                font-size: 15px;
            }
            #tabTitle {
                color: #9cff57;
                background: #071313;
                border-bottom: 2px solid #ff8a00;
                padding: 8px 12px;
                font-family: "Cascadia Code";
                font-weight: 700;
            }
            #projectTree {
                background: #050909;
                border: 1px solid #163737;
                border-radius: 6px;
                color: #eaf2f2;
                outline: 0;
            }
            #projectTree::item {
                min-height: 27px;
                padding-left: 4px;
            }
            #projectTree::item:selected {
                background: #442400;
                color: #ffb347;
                border: 1px solid #ff8a00;
            }
            #editor {
                background: #020606;
                color: #e8f1f2;
                border: 1px solid #173333;
                selection-background-color: #234d20;
                padding: 8px;
            }
            #output {
                background: #020606;
                color: #d9e6e6;
                border: 1px solid #173333;
                font-family: "Cascadia Code";
                font-size: 12px;
            }
            #toolButton {
                background: #2a1400;
                color: #ffae42;
                border: 2px solid #ff7a00;
                border-radius: 8px;
                font-size: 16px;
                font-weight: 800;
                text-align: left;
                padding: 8px 14px;
            }
            #toolButton:hover {
                background: #4a2400;
                color: #ffd08a;
                border-color: #ffb000;
            }
            #toolButton:pressed {
                background: #6b3200;
            }
            QPushButton {
                background: #0b1515;
                color: #f7f7f7;
                border: 1px solid #335555;
                border-radius: 7px;
                padding: 8px 12px;
            }
            QPushButton:hover {
                border-color: #39ff14;
                color: #9cff57;
            }
            #sendButton {
                min-width: 170px;
                min-height: 54px;
                background: #ff7a00;
                color: #120900;
                border: 1px solid #ffb000;
                font-size: 17px;
                font-weight: 900;
            }
            #sendButton:hover {
                background: #ff9a1f;
            }
            #prompt, QComboBox {
                background: #020606;
                color: #f5f7f8;
                border: 2px solid #39ff14;
                border-radius: 7px;
                padding: 8px 10px;
                min-height: 28px;
                font-family: "Cascadia Code";
            }
            #muted {
                color: #7f9292;
                font-size: 11px;
            }
            QSplitter::handle {
                background: #0a1d1d;
                width: 4px;
            }
            QScrollBar:vertical {
                background: #071010;
                width: 12px;
            }
            QScrollBar::handle:vertical {
                background: #39ff14;
                min-height: 30px;
                border-radius: 5px;
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
        worker.status.connect(lambda msg: self._log(msg, "CODEX"))
        worker.finished.connect(self._codex_finished)
        worker.failed.connect(self._codex_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self.worker_thread = thread
        self.worker = worker
        thread.start()

    def _codex_finished(self, payload):
        result, outputs = payload
        self.prompt.setEnabled(True)
        self._set_tools_enabled(True)

        if not result.ok:
            self._log(result.message, "ERROR")
            QMessageBox.critical(self, "Erreur CODEX", result.message)
            return

        self._log(result.message, "SUCCESS")
        changed = list(result.changed or [])
        self._load_project(self.project_root)

        if changed:
            first = (self.project_root / changed[0]).resolve()
            if first.is_file():
                self.open_file(first)

        self.prompt.setFocus()

    def _codex_failed(self, message: str):
        self.prompt.setEnabled(True)
        self._set_tools_enabled(True)
        self._log(message, "ERROR")
        QMessageBox.critical(self, "Erreur CODEX", message)
        self.prompt.setFocus()

    def _set_tools_enabled(self, enabled: bool):
        for btn in (self.btn_run, self.btn_save, self.btn_test, self.btn_build, self.btn_open, self.btn_zip):
            btn.setEnabled(enabled)


def main():
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    app = QApplication(sys.argv)
    app.setApplicationName("TI-LEX CODEX")
    app.setOrganizationName("Marceau")
    window = TiLexCodexWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
