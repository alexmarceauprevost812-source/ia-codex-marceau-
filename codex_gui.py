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
from PySide6.QtCore import QEasingCurve, QObject, QParallelAnimationGroup, QPropertyAnimation, QRect, QRectF, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QLinearGradient, QPainter, QPen, QPixmap, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QInputDialog,
    QMenu,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QProgressBar,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from codex_engine import CodexEngine
from config import load_config
from secret_store import SecretStore


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


class ReflectionSpinner(QWidget):
    """Gros anneau néon animé pour visualiser le moteur de réflexion."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.angle = 0
        self.running = False
        self.accent_color = "#39ff14"
        self.setFixedSize(76, 76)

        self.timer = QTimer(self)
        self.timer.setInterval(24)
        self.timer.timeout.connect(self._tick)

    def set_accent_color(self, color: str):
        self.accent_color = str(color or "#39ff14")
        self.update()

    def start(self):
        self.running = True
        if not self.timer.isActive():
            self.timer.start()
        self.update()

    def stop(self):
        self.running = False
        self.timer.stop()
        self.angle = 0
        self.update()

    def _tick(self):
        self.angle = (self.angle + 8) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        accent = QColor(self.accent_color)
        rect = QRectF(8, 8, self.width() - 16, self.height() - 16)

        # Anneau de fond discret.
        base = QColor(accent)
        base.setAlpha(55)
        painter.setPen(QPen(base, 5))
        painter.drawEllipse(rect)

        # Arc lumineux animé.
        bright = QColor(accent)
        bright.setAlpha(255)
        painter.setPen(QPen(bright, 7))
        if self.running:
            painter.drawArc(rect, int(-self.angle * 16), int(110 * 16))
        else:
            painter.drawArc(rect, int(35 * 16), int(70 * 16))

        # Petit noyau central.
        center = QColor(accent)
        center.setAlpha(210 if self.running else 110)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(center)
        painter.drawEllipse(self.rect().center(), 5, 5)
        painter.end()


class ProjectedTitle(QWidget):
    """Titre TI-LEX avec un faisceau lumineux qui traverse les lettres."""

    def __init__(self, text="▲  TI-LEX CODEX", parent=None):
        super().__init__(parent)
        self.text = text
        self.reflecting = False
        self.offset = -180.0
        self.font_family = "Ink Free"
        self.accent_color = "#39ff14"

        self.timer = QTimer(self)
        self.timer.setInterval(20)
        self.timer.timeout.connect(self._tick)

        self.setMinimumWidth(420)
        self.setFixedHeight(58)

    def set_font_family(self, family: str):
        self.font_family = str(family or "Ink Free")
        self.update()

    def set_accent_color(self, color: str):
        self.accent_color = str(color or "#39ff14")
        self.update()

    def set_reflecting(self, enabled: bool):
        self.reflecting = bool(enabled)
        if self.reflecting:
            if not self.timer.isActive():
                self.timer.start()
        else:
            self.timer.stop()
            self.offset = -180.0
        self.update()

    def _tick(self):
        self.offset += 12.0
        if self.offset > self.width() + 180:
            self.offset = -180.0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        font = QFont(self.font_family)
        font.setPointSize(26)
        font.setWeight(QFont.Weight.Bold)
        painter.setFont(font)

        text_rect = self.rect().adjusted(4, 0, -4, 0)

        # Texte principal.
        painter.setPen(QColor(self.accent_color))
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.text,
        )

        if not self.reflecting:
            painter.end()
            return

        # Faisceau "projecteur" qui traverse uniquement les lettres.
        band = QRectF(self.offset, 0, 150, self.height())
        gradient = QLinearGradient(band.left(), 0, band.right(), 0)
        accent = QColor(self.accent_color)
        transparent = QColor(accent)
        transparent.setAlpha(0)
        soft = QColor(accent)
        soft.setAlpha(90)
        bright = QColor(accent)
        bright.setAlpha(255)
        gradient.setColorAt(0.00, transparent)
        gradient.setColorAt(0.25, soft)
        gradient.setColorAt(0.50, bright)
        gradient.setColorAt(0.75, soft)
        gradient.setColorAt(1.00, transparent)

        painter.save()
        painter.setClipRect(band)
        painter.setPen(QColor(self.accent_color))
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.text,
        )
        painter.fillRect(band, gradient)
        painter.restore()
        painter.end()


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

    def set_accent_color(self, color: str):
        accent = QColor(str(color or "#39ff14"))
        self.added.setForeground(accent)
        self.header.setForeground(accent)
        self.file.setForeground(accent)
        self.rehighlight()

    def highlightBlock(self, text: str):
        if text.startswith("+++ ") or text.startswith("--- ") or text.startswith("@@"):
            self.setFormat(0, len(text), self.header)
        elif text.startswith("+") and not text.startswith("+++"):
            self.setFormat(0, len(text), self.added)
        elif text.startswith("-") and not text.startswith("---"):
            self.setFormat(0, len(text), self.removed)
        elif text.startswith("FILE "):
            self.setFormat(0, len(text), self.file)



class MarceauBackground(QWidget):
    """Fond TI-LEX MARCEAU avec image à 40 % et fallback graphique."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.background_pixmap = QPixmap()
        self.background_opacity = 0.40
        self.background_path: Path | None = None
        self.setObjectName("marceauRoot")
        self.setAutoFillBackground(False)

    def set_background_image(self, path: Path | str | None):
        self.background_path = Path(path).expanduser().resolve() if path else None
        self.background_pixmap = QPixmap()
        if self.background_path and self.background_path.is_file():
            self.background_pixmap.load(str(self.background_path))
        self.update()

    def set_background_opacity(self, opacity: float):
        self.background_opacity = max(0.0, min(1.0, float(opacity)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#030303"))

        if not self.background_pixmap.isNull():
            scaled = self.background_pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (scaled.width() - self.width()) // 2
            y = (scaled.height() - self.height()) // 2
            source = QRect(x, y, self.width(), self.height())
            painter.setOpacity(self.background_opacity)
            painter.drawPixmap(self.rect(), scaled, source)
            painter.setOpacity(1.0)
        else:
            # Fallback intégré si l'image originale n'est pas encore sur la machine.
            painter.setOpacity(0.40)
            orange = QColor("#ff7a00")
            orange.setAlpha(210)
            painter.setPen(QPen(orange, 4))
            big = QFont("Arial Black", max(64, min(self.width(), self.height()) // 5))
            big.setBold(True)
            painter.setFont(big)
            painter.drawText(
                self.rect().adjusted(20, -20, -20, 20),
                Qt.AlignmentFlag.AlignCenter,
                "M",
            )
            logo = QFont("Arial Black", max(30, min(self.width(), self.height()) // 14))
            logo.setBold(True)
            painter.setFont(logo)
            painter.drawText(
                self.rect().adjusted(20, 150, -20, -20),
                Qt.AlignmentFlag.AlignCenter,
                "MARCEAU",
            )
            painter.setOpacity(1.0)

        painter.end()


class CodexWorker(QObject):
    status = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, root: Path, request: str, model: str, provider: str = "OLLAMA",
                 anthropic_model: str | None = None,
                 preferred_target: str | None = None):
        super().__init__()
        self.root = root
        self.request = request
        self.model = model
        self.provider = str(provider or "OLLAMA").upper()
        self.anthropic_model = anthropic_model
        self.preferred_target = preferred_target

    def run(self):
        try:
            engine = CodexEngine(
                self.root,
                model=self.model,
                status=lambda msg: self.status.emit(str(msg)),
                provider=self.provider,
                anthropic_model=self.anthropic_model,
                preferred_target=self.preferred_target,
            )
            if self.request.strip().lower().startswith("/chat"):
                result = engine.chat(self.request)
                chat_mode = True
            else:
                result = engine.build(self.request)
                chat_mode = False

            self.finished.emit((
                result,
                {
                    "outputs": dict(engine.last_outputs),
                    "stats": dict(engine.last_stats_by_file),
                    "diffs": dict(engine.last_diffs),
                    "chat_mode": chat_mode,
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
        self.display_theme = "NOIR"
        self.neon_accent = "#ff7a00"
        self.display_font = "Ink Free"
        self.agent_provider = "OLLAMA"
        self.anthropic_model = "claude-sonnet-5-5"
        self.secret_store = SecretStore()
        self.marceau_background_path: Path | None = None
        self.typewriter_timer = QTimer(self)
        self.typewriter_timer.setInterval(12)
        self.typewriter_timer.timeout.connect(self._typewriter_step)
        self.typewriter_text = ""
        self.typewriter_index = 0
        self.typewriter_chunk = 1

        # Transition fluide entre fichiers: dézoom -> glisse -> rezoom.
        self.file_transition_active = False
        self.pending_file_path: Path | None = None
        self.file_transition_overlay = None
        self.file_transition_backdrop = None
        self.file_transition_group = None

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
        self._reload_marceau_background()
        if hasattr(self, "brand"):
            self.brand.set_font_family(self.display_font)
            self.brand.set_accent_color(self.neon_accent)
        if hasattr(self, "reflexion_spinner"):
            self.reflexion_spinner.set_accent_color(self.neon_accent)
        if hasattr(self, "diff_highlighter"):
            self.diff_highlighter.set_accent_color(self.neon_accent)
        self._apply_theme()
        self._apply_display_overrides()
        self._load_project(self.project_root)
        self._refresh_ollama_status()

    def _build_ui(self):
        root = MarceauBackground()
        self.marceau_background = root
        self.marceau_background_path = self._find_marceau_background()
        root.set_background_opacity(0.40)
        if self.marceau_background_path:
            root.set_background_image(self.marceau_background_path)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(7)

        # ===== HEADER =====
        header = QFrame(objectName="header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 10, 18, 10)
        header_layout.setSpacing(14)

        # Menu AFFICHAGE toujours visible, complètement à gauche.
        self.display_button = QToolButton()
        self.display_button.setObjectName("displayMenuButton")
        self.display_button.setText("☰  AFFICHAGE")
        self.display_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        display_menu = QMenu(self.display_button)
        display_menu.setObjectName("displayMenu")

        theme_menu = display_menu.addMenu("Fond")
        for label, key in (
            ("Noir", "NOIR"),
            ("Gris mat", "GRIS"),
            ("Blanc", "BLANC"),
        ):
            action = QAction(label, self)
            action.triggered.connect(lambda checked=False, value=key: self._set_display_theme(value))
            theme_menu.addAction(action)

        background_menu = display_menu.addMenu("Fond MARCEAU (40 %)")
        choose_background = QAction("Choisir l’image MARCEAU…", self)
        choose_background.triggered.connect(self._choose_marceau_background)
        background_menu.addAction(choose_background)

        reload_background = QAction("Recharger le fond", self)
        reload_background.triggered.connect(self._reload_marceau_background)
        background_menu.addAction(reload_background)

        neon_menu = display_menu.addMenu("Couleur néon")
        for label, value in (
            ("Vert néon", "#39ff14"),
            ("Orange néon", "#ff7a00"),
            ("Cyan néon", "#00efff"),
            ("Rouge néon", "#ff1744"),
            ("Jaune néon", "#fff200"),
            ("Rose néon", "#ff2bd6"),
            ("Violet néon", "#9d4dff"),
        ):
            action = QAction(label, self)
            action.triggered.connect(lambda checked=False, color=value: self._set_neon_accent(color))
            neon_menu.addAction(action)

        font_menu = display_menu.addMenu("Police d’écriture")
        for label, family in (
            ("Ink Free", "Ink Free"),
            ("Segoe Print", "Segoe Print"),
            ("Comic Sans MS", "Comic Sans MS"),
            ("Cascadia Code", "Cascadia Code"),
            ("Consolas", "Consolas"),
            ("Arial", "Arial"),
        ):
            action = QAction(label, self)
            action.triggered.connect(lambda checked=False, font=family: self._set_display_font(font))
            font_menu.addAction(action)

        self.display_button.setMenu(display_menu)
        header_layout.addWidget(self.display_button, 0, Qt.AlignmentFlag.AlignLeft)

        brand_box = QVBoxLayout()
        brand_box.setSpacing(0)
        self.brand = ProjectedTitle("▲  TI-LEX CODEX")
        self.brand.setObjectName("brand")
        subtitle = QLabel("IA Codex Marceau")
        subtitle.setObjectName("subtitle")
        brand_box.addWidget(self.brand)
        brand_box.addWidget(subtitle)

        header_layout.addLayout(brand_box)
        header_layout.addStretch(1)

        header_nav = QLabel("Développer   •   Analyser   •   Automatiser   •   Sans limites")
        header_nav.setObjectName("headerNav")
        header_nav.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(header_nav, 2)

        header_layout.addStretch(1)

        # ===== AGENT SELECTOR =====
        self.agent_button = QToolButton()
        self.agent_button.setObjectName("agentMenuButton")
        self.agent_button.setText("🤖  AGENT : OLLAMA")
        self.agent_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        agent_menu = QMenu(self.agent_button)
        agent_menu.setObjectName("agentMenu")

        ollama_action = QAction("OLLAMA LOCAL", self)
        ollama_action.triggered.connect(
            lambda checked=False: self._set_agent_provider("OLLAMA", "OLLAMA LOCAL")
        )
        agent_menu.addAction(ollama_action)

        claude_models = (
            ("HAIKU 5.5", "claude-haiku-5-5"),
            ("SONNET 5", "claude-sonnet-5"),
            ("SONNET 5.5", "claude-sonnet-5-5"),
            ("OPUS 4.8", "claude-opus-4-8"),
            ("OPUS 5", "claude-opus-5"),
        )

        claude_menu = agent_menu.addMenu("CLAUDE")
        for model_label, model_id in claude_models:
            action = QAction(model_label, self)
            action.triggered.connect(
                lambda checked=False, m=model_id, name=model_label:
                    self._set_claude_agent("CLAUDE", m, name)
            )
            claude_menu.addAction(action)

        claude_control_menu = agent_menu.addMenu("CLAUDE + CONTRÔLE")
        for model_label, model_id in claude_models:
            action = QAction(model_label, self)
            action.triggered.connect(
                lambda checked=False, m=model_id, name=model_label:
                    self._set_claude_agent("CLAUDE_CONTROL", m, name)
            )
            claude_control_menu.addAction(action)

        for label, provider in (
            ("CHATGPT", "OPENAI"),
            ("CHATGPT + INTERNET", "OPENAI_WEB"),
            ("PERPLEXITY", "PERPLEXITY"),
            ("DEEPSEEK", "DEEPSEEK"),
            ("GEMINI", "GEMINI"),
        ):
            action = QAction(label, self)
            action.triggered.connect(
                lambda checked=False, p=provider, name=label: self._set_agent_provider(p, name)
            )
            agent_menu.addAction(action)
        self.agent_button.setMenu(agent_menu)
        header_layout.addWidget(self.agent_button)

        # ===== LOCAL API KEY VAULT =====
        self.api_key_button = QToolButton()
        self.api_key_button.setObjectName("apiKeyMenuButton")
        self.api_key_button.setText("🔑  CLÉS API")
        self.api_key_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        api_menu = QMenu(self.api_key_button)
        api_menu.setObjectName("apiKeyMenu")

        add_anthropic = QAction("Enregistrer clé Anthropic", self)
        add_anthropic.triggered.connect(lambda: self._save_api_key("anthropic", "Anthropic"))
        api_menu.addAction(add_anthropic)

        add_openai = QAction("Enregistrer clé OpenAI", self)
        add_openai.triggered.connect(lambda: self._save_api_key("openai", "OpenAI"))
        api_menu.addAction(add_openai)

        add_perplexity = QAction("Enregistrer clé Perplexity", self)
        add_perplexity.triggered.connect(lambda: self._save_api_key("perplexity", "Perplexity"))
        api_menu.addAction(add_perplexity)

        add_deepseek = QAction("Enregistrer clé DeepSeek", self)
        add_deepseek.triggered.connect(lambda: self._save_api_key("deepseek", "DeepSeek"))
        api_menu.addAction(add_deepseek)

        add_gemini = QAction("Enregistrer clé Gemini", self)
        add_gemini.triggered.connect(lambda: self._save_api_key("gemini", "Gemini"))
        api_menu.addAction(add_gemini)

        api_menu.addSeparator()

        status_keys = QAction("Voir le statut des clés", self)
        status_keys.triggered.connect(self._show_api_key_status)
        api_menu.addAction(status_keys)

        delete_anthropic = QAction("Supprimer clé Anthropic", self)
        delete_anthropic.triggered.connect(lambda: self._delete_api_key("anthropic", "Anthropic"))
        api_menu.addAction(delete_anthropic)

        delete_openai = QAction("Supprimer clé OpenAI", self)
        delete_openai.triggered.connect(lambda: self._delete_api_key("openai", "OpenAI"))
        api_menu.addAction(delete_openai)

        delete_perplexity = QAction("Supprimer clé Perplexity", self)
        delete_perplexity.triggered.connect(lambda: self._delete_api_key("perplexity", "Perplexity"))
        api_menu.addAction(delete_perplexity)

        delete_deepseek = QAction("Supprimer clé DeepSeek", self)
        delete_deepseek.triggered.connect(lambda: self._delete_api_key("deepseek", "DeepSeek"))
        api_menu.addAction(delete_deepseek)

        delete_gemini = QAction("Supprimer clé Gemini", self)
        delete_gemini.triggered.connect(lambda: self._delete_api_key("gemini", "Gemini"))
        api_menu.addAction(delete_gemini)

        self.api_key_button.setMenu(api_menu)
        header_layout.addWidget(self.api_key_button)

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

        # TI-LEX-AL bien exposé dans un cadre néon avec de l'espace autour.
        self.reflexion_avatar_frame = QFrame()
        self.reflexion_avatar_frame.setObjectName("reflexionAvatarFrame")
        self.reflexion_avatar_frame.setFixedSize(270, 175)

        avatar_frame_layout = QVBoxLayout(self.reflexion_avatar_frame)
        avatar_frame_layout.setContentsMargins(16, 14, 16, 14)
        avatar_frame_layout.setSpacing(0)

        self.reflexion_avatar = QLabel()
        self.reflexion_avatar.setObjectName("reflexionAvatar")
        self.reflexion_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.reflexion_avatar.setFixedSize(232, 142)

        demon_candidates = [
            Path(__file__).resolve().parent / "assets" / "reflexion_demon.png",
            Path(__file__).resolve().parent / "assets" / "tilex_al.png",
        ]
        demon_pixmap = QPixmap()
        for candidate in demon_candidates:
            if candidate.is_file() and demon_pixmap.load(str(candidate)):
                break

        if not demon_pixmap.isNull():
            # Recadrage léger: logo plus gros sans couper les cornes ou le texte.
            crop_w = max(1, int(demon_pixmap.width() * 0.86))
            crop_h = max(1, int(demon_pixmap.height() * 0.88))
            crop_x = max(0, (demon_pixmap.width() - crop_w) // 2)
            crop_y = max(0, (demon_pixmap.height() - crop_h) // 2)
            demon_cropped = demon_pixmap.copy(crop_x, crop_y, crop_w, crop_h)

            self.reflexion_avatar.setPixmap(
                demon_cropped.scaled(
                    222, 132,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            self.reflexion_avatar.setText("😈")

        avatar_frame_layout.addWidget(
            self.reflexion_avatar,
            0,
            Qt.AlignmentFlag.AlignCenter,
        )

        engine_text = QVBoxLayout()
        engine_text.setContentsMargins(0, 0, 0, 0)
        engine_text.setSpacing(1)

        self.engine_status = QLabel("● MOTEUR DE RÉFLEXION  •  PRÊT")
        self.engine_status.setObjectName("engineStatus")

        self.engine_detail = QLabel("En attente d’une commande…")
        self.engine_detail.setObjectName("engineDetail")

        self.engine_flow = QLabel(
            "ANALYSE  →  PLAN  →  CODE  →  ÉCRITURE FICHIERS  →  VALIDATION"
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

        self.reflexion_spinner = ReflectionSpinner()
        self.reflexion_spinner.setObjectName("reflectionSpinner")
        self.reflexion_spinner.set_accent_color(self.neon_accent)

        engine_layout.addWidget(self.reflexion_avatar_frame)
        engine_layout.addWidget(self.reflexion_spinner, 0, Qt.AlignmentFlag.AlignVCenter)
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
        prompt_title = QLabel("💬  Commande Codex / Chat IA")
        prompt_title.setObjectName("sectionTitle")
        self.prompt = QLineEdit()
        self.prompt.setObjectName("prompt")
        self.prompt.setPlaceholderText("Ex : /chat explique ce projet  •  ou demande une modification de code")
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
        self.mode_combo.addItems(["AUTO", "PRO", "PROJET", "DIRECT"])
        self.mode_combo.setToolTip(
            "PROJET : architecture multi-fichiers, validation du lot et sauvegarde avant écriture."
        )
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

    def _find_marceau_background(self) -> Path | None:
        """Trouve automatiquement l'image MARCEAU dans le projet ou les dossiers Windows usuels."""
        preferred_names = (
            "marceau_background.png",
            "marceau_background.jpg",
            "marceau.png",
            "MARCEAU.png",
            "Image Codex 22 sept. 2026, 11_15_11.png",
        )
        roots = (
            Path(__file__).resolve().parent / "assets",
            Path(__file__).resolve().parent,
            Path.home() / "Downloads",
            Path.home() / "Desktop",
            Path.home() / "Pictures",
        )

        for root in roots:
            for name in preferred_names:
                candidate = root / name
                if candidate.is_file():
                    return candidate

        for root in roots[2:]:
            if not root.is_dir():
                continue
            for pattern in ("*marceau*.png", "*marceau*.jpg", "*codex*.png", "*codex*.jpg"):
                try:
                    for candidate in sorted(root.glob(pattern)):
                        if candidate.is_file():
                            return candidate
                except OSError:
                    continue
        return None

    def _choose_marceau_background(self):
        start_dir = str(Path.home() / "Pictures")
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Choisir le fond MARCEAU",
            start_dir,
            "Images (*.png *.jpg *.jpeg *.webp)",
        )
        if not filename:
            return
        path = Path(filename).resolve()
        self.marceau_background_path = path
        self.marceau_background.set_background_image(path)
        self.marceau_background.set_background_opacity(0.40)
        try:
            state_dir = self.project_root / ".tilex"
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "background_path.txt").write_text(str(path), encoding="utf-8")
        except OSError:
            pass
        self._log(f"Fond MARCEAU chargé à 40 % : {path.name}", "SUCCESS")

    def _reload_marceau_background(self):
        saved = self.project_root / ".tilex" / "background_path.txt"
        path = None
        try:
            if saved.is_file():
                candidate = Path(saved.read_text(encoding="utf-8").strip()).expanduser()
                if candidate.is_file():
                    path = candidate
        except OSError:
            path = None

        path = path or self._find_marceau_background()
        self.marceau_background_path = path
        self.marceau_background.set_background_image(path)
        self.marceau_background.set_background_opacity(0.40)
        if path:
            self._log(f"Fond MARCEAU rechargé : {path.name}", "SUCCESS")
        else:
            self._log("Image MARCEAU introuvable : fallback graphique activé.", "WARN")

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

    def _set_display_theme(self, theme: str):
        self.display_theme = str(theme).upper()
        self._apply_theme()
        self._apply_display_overrides()

    def _set_neon_accent(self, color: str):
        self.neon_accent = color
        if hasattr(self, "brand"):
            self.brand.set_accent_color(self.neon_accent)
        if hasattr(self, "reflexion_spinner"):
            self.reflexion_spinner.set_accent_color(self.neon_accent)
        if hasattr(self, "diff_highlighter"):
            self.diff_highlighter.set_accent_color(self.neon_accent)
        self._apply_theme()
        self._apply_display_overrides()

    def _set_display_font(self, family: str):
        self.display_font = str(family or "Ink Free")
        if hasattr(self, "brand"):
            self.brand.set_font_family(self.display_font)
        self._apply_theme()
        self._apply_display_overrides()

    def _apply_display_overrides(self):
        accent = self.neon_accent
        theme = self.display_theme
        font = self.display_font

        if theme == "BLANC":
            bg = "#f4f4f4"
            panel = "#ffffff"
            text = "#151515"
            muted = "#444444"
            editor_bg = "#ffffff"
        elif theme == "GRIS":
            bg = "#1b1b1b"
            panel = "#242424"
            text = "#f2f2f2"
            muted = "#b0b0b0"
            editor_bg = "#202020"
        else:
            bg = "#030303"
            panel = "rgba(4, 4, 4, 205)"
            text = "#f4f4f4"
            muted = "#c2c2c2"
            editor_bg = "rgba(0, 0, 0, 165)"

        overrides = f"""
            QMainWindow {{
                background: {bg};
                color: {text};
            }}

            QWidget {{
                background: transparent;
                color: {text};
                font-family: "{font}";
            }}

            QWidget#marceauRoot {{
                background: transparent;
            }}

            #header, #enginePanel, #panel, #commandBar,
            #infoCard {{
                background: {panel};
                border: 1px solid {accent};
                border-radius: 10px;
            }}

            #projectTree, #editor, #output, #diffView,
            #tabTitle, #langBadge, #topStatus, #ollama {{
                background: {editor_bg};
            }}

            #reflexionAvatarFrame {{
                background: {panel};
                border: 2px solid {accent};
                border-radius: 16px;
            }}

            #reflexionAvatar {{
                background: transparent;
                border: none;
            }}

            #editor, #output, #diffView, #projectTree {{
                background: {editor_bg};
                color: {text};
                border: 1px solid {accent};
                border-radius: 8px;
                font-family: "{font}";
            }}

            #sectionTitle,
            #subtitle,
            #engineFlow,
            #engineDetail,
            #engineStatus,
            #langBadge,
            #versionLabel,
            #cardFooter,
            #tabTitle,
            #topStatus,
            #ollama,
            #resultsSummary,
            #cardLogo,
            #cardTitle,
            #headerNav {{
                color: {accent};
            }}

            #projectTree::item:hover {{
                color: {accent};
            }}

            #projectTree::item:selected {{
                background: {accent};
                color: #000000;
                border: none;
            }}

            #displayMenuButton,
            #agentMenuButton,
            #apiKeyMenuButton {{
                background: transparent;
                color: {accent};
                border: none;
                padding: 8px 10px;
                font-weight: 900;
                font-size: 13px;
            }}

            #displayMenuButton:hover,
            #agentMenuButton:hover,
            #apiKeyMenuButton:hover {{
                background: {panel};
                color: {accent};
            }}

            QMenu#displayMenu, QMenu {{
                background: {panel};
                color: {text};
                border: 1px solid {accent};
                padding: 5px;
            }}

            QMenu::item {{
                padding: 7px 22px 7px 10px;
            }}

            QMenu::item:selected {{
                background: {accent};
                color: #000000;
            }}

            QPushButton,
            #toolButton,
            #panelTabButton,
            #reopenPanelButton,
            #newProjectButton,
            #openProjectButton,
            #clearButton,
            #sendButton {{
                background: {panel};
                color: {accent};
                border: 1px solid {accent};
            }}

            QPushButton:hover,
            #toolButton:hover,
            #panelTabButton:hover,
            #reopenPanelButton:hover,
            #newProjectButton:hover,
            #openProjectButton:hover,
            #clearButton:hover,
            #sendButton:hover {{
                background: {accent};
                color: #000000;
                border: 1px solid {accent};
            }}

            QPushButton:pressed,
            #toolButton:pressed,
            #panelTabButton:pressed,
            #reopenPanelButton:pressed,
            #newProjectButton:pressed,
            #openProjectButton:pressed,
            #clearButton:pressed,
            #sendButton:pressed {{
                background: {accent};
                color: #ffffff;
                border: 1px solid {accent};
            }}

            #prompt, QComboBox {{
                color: {text};
                background: {panel};
                border: 1px solid {accent};
            }}

            #prompt:focus, QComboBox:focus {{
                border: 1px solid {accent};
            }}

            #engineProgress::chunk {{
                background: {accent};
            }}

            #muted {{
                color: {muted};
            }}

            QScrollBar::handle:vertical {{
                background: {accent};
            }}

            QScrollBar::handle:vertical:hover {{
                background: {accent};
            }}

            QSplitter::handle:hover {{
                background: {accent};
            }}
        """
        self.setStyleSheet(self.styleSheet() + overrides)

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
                color: #39ff14;
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
                color: #39ff14;
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

            #reflexionAvatarFrame {
                background: #000000;
                border: 2px solid #39ff14;
                border-radius: 16px;
            }

            #reflexionAvatar {
                background: transparent;
                color: #ff8a00;
                border: 2px solid #ff8a00;
                border-radius: 10px;
                padding: 0px;
                font-size: 34px;
                font-weight: 900;
            }

            #engineDetail {
                color: #baff9f;
                font-size: 11px;
                font-weight: 700;
            }

            #engineStatus {
                color: #ff9d21;
                font-weight: 900;
                font-size: 12px;
            }

            #engineFlow {
                color: #39ff14;
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
                color: #39ff14;
                border: 1px solid #00d9cc;
                border-radius: 7px;
                padding: 5px 10px;
                font-family: "Ink Free", "Segoe Print", "Comic Sans MS";
                font-size: 12px;
                font-weight: 700;
            }

            #langBadge {
                background: #000000;
                color: #39ff14;
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
                color: #39ff14;
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
                color: #39ff14;
                font-weight: 900;
                font-size: 15px;
                letter-spacing: 0.5px;
            }

            #tabTitle {
                color: #39ff14;
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
                background: #09220a;
                color: #39ff14;
            }

            #projectTree::item:selected {
                background: #123d08;
                color: #baff9f;
                border: 1px solid #39ff14;
            }

            #newProjectButton {
                background: #041304;
                color: #39ff14;
                border: 2px solid #39ff14;
                border-radius: 8px;
                padding: 8px;
                font-weight: 900;
            }

            #newProjectButton:hover {
                background: #0b2b06;
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
                color: #39ff14;
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
                color: #39ff14;
                font-size: 11px;
                font-weight: 700;
            }

            #versionLabel {
                color: #39ff14;
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
                selection-background-color: #163d1a;
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
                color: #39ff14;
                border-color: #39ff14;
            }

            #sendButton {
                min-width: 180px;
                min-height: 55px;
                background: #1a0900;
                color: #ff9d21;
                border: 1px solid #ff7a00;
                border-radius: 7px;
                font-size: 16px;
                font-weight: 900;
                padding: 8px 16px;
            }

            #sendButton:hover {
                background: #2a1000;
                color: #ffb347;
                border: 1px solid #ff9d21;
            }

            #sendButton:pressed {
                background: #3a1600;
                color: #ffd08a;
                border: 1px solid #ffb347;
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
                border: none;
            }

            /* ===== MODE PLAT TI-LEX =====
               Aucun cadre bleu/cyan ou vert lime. */
            #header,
            #enginePanel,
            #panel,
            #commandBar,
            #projectTree,
            #editor,
            #output,
            #diffView,
            #tabTitle,
            #langBadge,
            #topStatus,
            #ollama,
            #engineProgress,
            #infoCard,
            #reflexionAvatar,
            #panelTabButton,
            #reopenPanelButton,
            #newProjectButton,
            #clearButton,
            #prompt,
            QComboBox {
                border: none;
            }

            #header,
            #enginePanel,
            #panel,
            #commandBar,
            #projectTree,
            #editor,
            #output,
            #diffView,
            #tabTitle,
            #langBadge,
            #topStatus,
            #ollama,
            #infoCard,
            #reflexionAvatar {
                border-radius: 0px;
                background: #000000;
            }

            #reflexionAvatar {
                padding: 0px;
            }

            #engineProgress {
                background: #0a0a0a;
                border-radius: 4px;
            }

            #panelTabButton,
            #reopenPanelButton,
            #clearButton {
                background: #000000;
            }

            #panelTabButton:hover,
            #reopenPanelButton:hover,
            #clearButton:hover {
                background: #0d0d0d;
            }

            #newProjectButton {
                background: #041304;
                color: #39ff14;
            }

            #prompt,
            QComboBox {
                background: #050505;
            }

            #prompt:focus,
            QComboBox:focus {
                border: none;
            }

            QSplitter::handle {
                background: #000000;
                width: 2px;
            }

            QSplitter::handle:hover {
                background: #151515;
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
        if hasattr(self, "reflexion_spinner"):
            self.reflexion_spinner.start()
        if hasattr(self, "brand"):
            self.brand.set_reflecting(True)
        self._animate_thinking()

    def _stop_thinking_animation(self):
        if self.thinking_timer.isActive():
            self.thinking_timer.stop()
        if hasattr(self, "reflexion_spinner"):
            self.reflexion_spinner.stop()
        if hasattr(self, "brand"):
            self.brand.set_reflecting(False)
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

    def _apply_file_content(self, path: Path, content: str):
        """Charge réellement le fichier dans l'éditeur sans casser l'animation."""
        self.current_file = path
        self.editor.setPlainText(content)
        self._update_language_badge(path)
        try:
            rel = path.relative_to(self.project_root)
        except ValueError:
            rel = path
        self.file_title.setText(f"📄 {rel}")
        self._log(f"Fichier ouvert : {rel}")

    def _make_file_transition_overlay(self):
        parent = self.editor.parentWidget()
        base = self.editor.geometry()

        backdrop = QLabel(parent)
        backdrop.setStyleSheet("background:#000000; border:none;")
        backdrop.setGeometry(base)
        backdrop.show()
        backdrop.raise_()

        overlay = QLabel(parent)
        overlay.setPixmap(self.editor.grab())
        overlay.setScaledContents(True)
        overlay.setGeometry(base)
        overlay.setStyleSheet(
            "background:#000000; border:1px solid #39ff14; border-radius:6px;"
        )

        glow = QGraphicsDropShadowEffect(overlay)
        glow.setBlurRadius(32)
        glow.setOffset(0, 0)
        glow.setColor(QColor("#39ff14"))
        overlay.setGraphicsEffect(glow)

        overlay.show()
        overlay.raise_()

        self.file_transition_backdrop = backdrop
        self.file_transition_overlay = overlay
        return base

    def _run_file_transition_phase(
        self,
        start_rect: QRect,
        end_rect: QRect,
        start_opacity: float,
        end_opacity: float,
        finished,
    ):
        overlay = self.file_transition_overlay
        if overlay is None:
            finished()
            return

        opacity = QGraphicsOpacityEffect(overlay)
        opacity.setOpacity(start_opacity)
        overlay.setGraphicsEffect(opacity)
        overlay.setGeometry(start_rect)

        move = QPropertyAnimation(overlay, b"geometry", self)
        move.setDuration(230)
        move.setStartValue(start_rect)
        move.setEndValue(end_rect)
        move.setEasingCurve(QEasingCurve.Type.InOutCubic)

        fade = QPropertyAnimation(opacity, b"opacity", self)
        fade.setDuration(230)
        fade.setStartValue(start_opacity)
        fade.setEndValue(end_opacity)
        fade.setEasingCurve(QEasingCurve.Type.InOutCubic)

        group = QParallelAnimationGroup(self)
        group.addAnimation(move)
        group.addAnimation(fade)
        group.finished.connect(finished)
        self.file_transition_group = group
        group.start()

    def _start_file_transition(self, path: Path, content: str):
        self.file_transition_active = True
        base = self._make_file_transition_overlay()

        # Phase 1: petit dézoom + départ horizontal vers la gauche.
        shrink_w = max(120, int(base.width() * 0.90))
        shrink_h = max(80, int(base.height() * 0.90))
        out_rect = QRect(
            base.x() - int(base.width() * 0.22),
            base.y() + int(base.height() * 0.05),
            shrink_w,
            shrink_h,
        )

        def switch_to_new_file():
            self._apply_file_content(path, content)
            QApplication.processEvents()

            overlay = self.file_transition_overlay
            if overlay is None:
                self._finish_file_transition()
                return

            # Nouveau fichier: capture puis arrivée de droite, légèrement dézoomée.
            overlay.setPixmap(self.editor.grab())
            incoming = QRect(
                base.x() + int(base.width() * 0.22),
                base.y() + int(base.height() * 0.05),
                shrink_w,
                shrink_h,
            )
            self._run_file_transition_phase(
                incoming,
                base,
                0.20,
                1.0,
                self._finish_file_transition,
            )

        self._run_file_transition_phase(
            base,
            out_rect,
            1.0,
            0.18,
            switch_to_new_file,
        )

    def _finish_file_transition(self):
        if self.file_transition_overlay is not None:
            self.file_transition_overlay.deleteLater()
            self.file_transition_overlay = None
        if self.file_transition_backdrop is not None:
            self.file_transition_backdrop.deleteLater()
            self.file_transition_backdrop = None

        self.file_transition_group = None
        self.file_transition_active = False

        pending = self.pending_file_path
        self.pending_file_path = None
        if pending is not None and pending != self.current_file:
            QTimer.singleShot(20, lambda p=pending: self.open_file(p))

    def open_file(self, path: Path):
        if self.typewriter_timer.isActive():
            self.typewriter_timer.stop()
            self.editor.setReadOnly(False)

        path = Path(path).resolve()

        if self.file_transition_active:
            self.pending_file_path = path
            return

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return

        # Premier fichier ou réouverture du même fichier: pas besoin de transition.
        if self.current_file is None or self.current_file == path or self.editor.width() < 100:
            self._apply_file_content(path, content)
            return

        self._start_file_transition(path, content)

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

    def _set_agent_provider(self, provider: str, label: str):
        self.agent_provider = str(provider or "OLLAMA").upper()
        self.agent_button.setText(f"🤖  AGENT : {label}")
        self._log(f"Agent sélectionné : {label}", "INFO")

    def _set_claude_agent(self, provider: str, model_id: str, model_label: str):
        self.agent_provider = str(provider or "CLAUDE").upper()
        self.anthropic_model = str(model_id or "claude-sonnet-5-5")
        prefix = "CLAUDE + CONTRÔLE" if self.agent_provider == "CLAUDE_CONTROL" else "CLAUDE"
        self.agent_button.setText(f"🤖  AGENT : {prefix} / {model_label}")
        self._log(
            f"Agent sélectionné : {prefix} • modèle {model_label} ({self.anthropic_model})",
            "INFO",
        )

    def _save_api_key(self, provider: str, label: str):
        try:
            value, ok = QInputDialog.getText(
                self,
                f"Clé API {label}",
                f"Entre ta clé API {label}. Elle sera enregistrée seulement dans le coffre sécurisé de cet appareil.",
                QLineEdit.EchoMode.Password,
            )
            if not ok:
                return
            value = str(value or "").strip()
            if not value:
                QMessageBox.warning(self, "Clé API", "Aucune clé entrée.")
                return
            self.secret_store.set_api_key(provider, value)
            self._log(f"Clé {label} enregistrée dans le coffre local.", "SUCCESS")
            QMessageBox.information(
                self,
                "Clé API",
                f"Clé {label} enregistrée localement dans le coffre sécurisé du système.",
            )
        except Exception as exc:
            self._log(f"Coffre API : {exc}", "ERROR")
            QMessageBox.critical(self, "Erreur coffre API", str(exc))

    def _show_api_key_status(self):
        try:
            anthropic = self.secret_store.status("anthropic")
            openai = self.secret_store.status("openai")
            perplexity = self.secret_store.status("perplexity")
            deepseek = self.secret_store.status("deepseek")
            gemini = self.secret_store.status("gemini")
            text = (
                f"Anthropic : {'CONFIGURÉE' if anthropic.configured else 'ABSENTE'}\n"
                f"OpenAI : {'CONFIGURÉE' if openai.configured else 'ABSENTE'}\n"
                f"Perplexity : {'CONFIGURÉE' if perplexity.configured else 'ABSENTE'}\n"
                f"DeepSeek : {'CONFIGURÉE' if deepseek.configured else 'ABSENTE'}\n"
                f"Gemini : {'CONFIGURÉE' if gemini.configured else 'ABSENTE'}\n\n"
                f"Coffre : {openai.backend}"
            )
            QMessageBox.information(self, "Statut des clés API", text)
        except Exception as exc:
            QMessageBox.critical(self, "Erreur coffre API", str(exc))

    def _delete_api_key(self, provider: str, label: str):
        answer = QMessageBox.question(
            self,
            "Supprimer la clé",
            f"Supprimer la clé API {label} de cet appareil ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            deleted = self.secret_store.delete_api_key(provider)
            if deleted:
                self._log(f"Clé {label} supprimée du coffre local.", "INFO")
                QMessageBox.information(self, "Clé API", f"Clé {label} supprimée.")
            else:
                QMessageBox.information(self, "Clé API", f"Aucune clé {label} enregistrée.")
        except Exception as exc:
            QMessageBox.critical(self, "Erreur coffre API", str(exc))

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
            is_chat = request.lower().startswith("/chat")
            if is_chat:
                self._set_engine_stage("CHAT IA", "préparation de la réponse", 15)
            else:
                self._set_engine_stage("ANALYSE", "compréhension de la commande", 10)
            self._start_thinking_animation()

            mode = self.mode_combo.currentText()
            if not is_chat:
                if mode == "PRO":
                    request = "/pro " + request
                elif mode == "PROJET":
                    request = "/project " + request
                elif mode == "DIRECT":
                    request = "/fast " + request

            self.prompt.clear()
            self.prompt.setEnabled(False)
            self._set_tools_enabled(False)
            self._log(f"Commande : {request}")
            self._log("CODEX travaille…", "INFO")

            preferred_target = None
            if self.current_file:
                try:
                    preferred_target = str(
                        self.current_file.resolve().relative_to(self.project_root)
                    ).replace("\\", "/")
                    if any(
                        part.lower() in {"backup", "backups", "sauvegarde", "sauvegardes", ".tilex"}
                        for part in Path(preferred_target).parts
                    ):
                        self._log(
                            f"Fichier ouvert ignoré comme cible automatique : {preferred_target}",
                            "WARN",
                        )
                    else:
                        self._log(
                            f"Fichier cible prioritaire : {preferred_target}",
                            "INFO",
                        )
                except (ValueError, OSError):
                    preferred_target = None

            thread = QThread(self)
            worker = CodexWorker(
                self.project_root,
                request,
                self.config.get("model", "qwen2.5:7b"),
                self.agent_provider,
                self.anthropic_model,
                preferred_target,
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

            chat_mode = bool(run_data.get("chat_mode")) if isinstance(run_data, dict) else False
            if chat_mode or str((result.plan or {}).get("mode", "")).upper() == "CHAT":
                self._log("IA : " + result.message, "CHAT")
                self._set_engine_stage("CHAT IA", "réponse terminée", 100)
                self._stop_thinking_animation()
                self._show_right_panel(0)
                self.prompt.setFocus()
                return

            self._log(result.message, "SUCCESS")
            changed = list(result.changed or [])
            self._set_engine_stage("VALIDATION", "fichiers du projet appliqués", 92)

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
