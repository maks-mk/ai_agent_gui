from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import qtawesome as qta
from PySide6.QtCore import QAbstractListModel, QMimeData, QModelIndex, QPoint, QRect, QRegularExpression, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QKeyEvent, QPainter, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionViewItem,
    QTextBrowser,
    QToolButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from core.model_profiles import ALLOWED_PROVIDERS, generate_profile_id, normalize_profiles_payload, sanitize_profile_id

from core.ui_theme import (
    ACCENT_BLUE,
    AMBER_WARNING,
    BORDER,
    ERROR_RED,
    MONO_FONT_FAMILY,
    SURFACE_ALT,
    SURFACE_BG,
    SUCCESS_GREEN,
    SURFACE_CARD,
    TEXT_MUTED,
    TEXT_PRIMARY,
)

FENCED_BLOCK_RE = re.compile(r"```([\w+-]*)\r?\n(.*?)```", re.DOTALL)
DIFF_HUNK_HEADER_RE = re.compile(r"^@@ -(?P<old>\d+)(?:,\d+)? \+(?P<new>\d+)(?:,\d+)? @@")
TRANSCRIPT_MAX_WIDTH = 1180
USER_MESSAGE_COLLAPSE_CHAR_LIMIT = 420
USER_MESSAGE_COLLAPSE_LINE_LIMIT = 8
COMPOSER_MENTION_MAX_ITEMS = 50
COMPOSER_MENTION_EXCLUDED_DIRS = {".git", "venv", "__pycache__", ".agent_state", "dist"}
COMPOSER_MENTION_POPUP_MIN_WIDTH = 560
COMPOSER_MENTION_POPUP_MAX_WIDTH = 700


def _make_mono_font() -> QFont:
    font = QFont(MONO_FONT_FAMILY)
    if not font.exactMatch():
        font = QFont("Consolas")
    font.setStyleHint(QFont.Monospace)
    font.setPointSize(10)
    return font


def _fa_icon(name: str, *, color: str = TEXT_MUTED, size: int = 14, **kwargs: Any) -> QIcon:
    safe_size = max(8, int(size))
    icon = qta.icon(name, color=color, **kwargs)
    pixmap = icon.pixmap(safe_size, safe_size)
    if pixmap.isNull():
        return icon
    return QIcon(pixmap)


def _collapsed_user_message_text(text: str) -> tuple[str, bool]:
    raw_text = str(text)
    preview_lines = raw_text.splitlines()
    line_limited = len(preview_lines) > USER_MESSAGE_COLLAPSE_LINE_LIMIT
    preview = "\n".join(preview_lines[:USER_MESSAGE_COLLAPSE_LINE_LIMIT]) if line_limited else raw_text
    char_limited = len(preview) > USER_MESSAGE_COLLAPSE_CHAR_LIMIT
    if char_limited:
        preview = preview[: USER_MESSAGE_COLLAPSE_CHAR_LIMIT].rstrip()
    is_collapsed = line_limited or char_limited or len(preview) < len(raw_text)
    if is_collapsed:
        preview = preview.rstrip()
        if preview:
            preview += "…"
    return preview or raw_text, is_collapsed


def _sync_plain_text_height(
    editor: QPlainTextEdit,
    *,
    min_lines: int = 2,
    max_lines: int = 12,
    extra_padding: int = 16,
) -> None:
    metrics = QFontMetrics(editor.font())
    line_count = max(min_lines, min(editor.blockCount(), max_lines))
    editor.setFixedHeight(line_count * metrics.lineSpacing() + extra_padding)


def _normalize_display_path(path_value: str) -> str:
    value = str(path_value or "").strip()
    if not value:
        return ""
    try:
        candidate = Path(value)
        if candidate.is_absolute():
            try:
                return candidate.relative_to(Path.cwd()).as_posix()
            except ValueError:
                return candidate.as_posix()
        return candidate.as_posix()
    except Exception:
        return value.replace("\\", "/")


def _extract_diff_path(diff_text: str, fallback_path: str = "") -> str:
    fallback = _normalize_display_path(fallback_path)
    if fallback:
        return fallback
    for raw_line in str(diff_text or "").splitlines():
        line = raw_line.strip()
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                right = parts[3].removeprefix("b/")
                if right:
                    return right
        if line.startswith("+++ "):
            candidate = line[4:].strip().removeprefix("b/")
            if candidate and candidate != "/dev/null":
                return candidate
    return "diff"


def _render_diff_with_line_numbers(diff_text: str) -> tuple[str, int, int]:
    lines = str(diff_text or "").splitlines()
    if not lines:
        return "", 0, 0

    rendered_lines: list[str] = []
    added = 0
    removed = 0
    old_line: int | None = None
    new_line: int | None = None

    for raw_line in lines:
        line = raw_line.rstrip("\n")
        hunk_match = DIFF_HUNK_HEADER_RE.match(line)
        if hunk_match:
            old_line = int(hunk_match.group("old"))
            new_line = int(hunk_match.group("new"))
            continue

        first_char = line[:1]
        is_add = first_char == "+"
        is_del = first_char == "-"
        is_meta = (
            line.startswith("@@")
            or line.startswith("diff --git")
            or line.startswith("index ")
            or line.startswith("--- ")
            or line.startswith("+++ ")
            or line.startswith("\\ No newline")
        )

        if is_meta:
            continue

        marker = " "
        payload = line
        if is_add:
            marker = "+"
            payload = line[1:]
        elif is_del:
            marker = "-"
            payload = line[1:]
        elif line.startswith(" "):
            payload = line[1:]

        old_number = ""
        new_number = ""
        if is_add:
            added += 1
            new_number = str(new_line) if new_line is not None else ""
            rendered_lines.append(f"{old_number:>6} {new_number:>6} {marker} {payload}")
            if new_line is not None:
                new_line += 1
            continue

        if is_del:
            removed += 1
            old_number = str(old_line) if old_line is not None else ""
            rendered_lines.append(f"{old_number:>6} {new_number:>6} {marker} {payload}")
            if old_line is not None:
                old_line += 1
            continue

        old_number = str(old_line) if old_line is not None else ""
        new_number = str(new_line) if new_line is not None else ""
        rendered_lines.append(f"{old_number:>6} {new_number:>6} {marker} {payload}")
        if old_line is not None:
            old_line += 1
        if new_line is not None:
            new_line += 1

    return "\n".join(rendered_lines), added, removed


class AutoTextBrowser(QTextBrowser):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._height_sync_pending = False
        self._last_markdown = ""
        self._last_height = 0
        self.setFrameShape(QFrame.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.document().setDocumentMargin(0)
        self.document().documentLayout().documentSizeChanged.connect(self._queue_height_sync)

    def setMarkdown(self, markdown: str) -> None:  # type: ignore[override]
        if markdown == self._last_markdown:
            return
        self._last_markdown = markdown
        super().setMarkdown(markdown)
        self._queue_height_sync()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._queue_height_sync()

    def _queue_height_sync(self, *_args) -> None:
        if self._height_sync_pending:
            return
        self._height_sync_pending = True
        QTimer.singleShot(0, self._sync_height)

    def _sync_height(self) -> None:
        self._height_sync_pending = False
        try:
            document = self.document()
            layout = document.documentLayout() if document is not None else None
            if layout is None:
                return
            doc_height = int(layout.documentSize().height())
        except RuntimeError:
            # A queued resize sync can fire after the Qt object was already deleted.
            return
        target_height = max(28, doc_height + 8)
        if target_height == self._last_height:
            return
        self._last_height = target_height
        try:
            self.setFixedHeight(target_height)
            self.updateGeometry()
        except RuntimeError:
            return


class ElidedLabel(QLabel):
    def __init__(self, parent: QWidget | None = None, *, elide_mode: Qt.TextElideMode = Qt.ElideRight) -> None:
        super().__init__(parent)
        self._full_text = ""
        self._elide_mode = elide_mode
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setMinimumWidth(0)

    def set_full_text(self, text: str) -> None:
        self._full_text = str(text or "")
        self._update_elided_text()

    def full_text(self) -> str:
        return self._full_text

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_elided_text()

    def _update_elided_text(self) -> None:
        text = self._full_text
        if not text:
            super().setText("")
            self.setToolTip("")
            return
        available = max(0, self.contentsRect().width())
        if available <= 0:
            super().setText("")
            self.setToolTip(text)
            return
        elided = self.fontMetrics().elidedText(text, self._elide_mode, available)
        super().setText(elided)
        self.setToolTip(text if elided != text else "")


class CodeHighlighter(QSyntaxHighlighter):
    def __init__(self, document, language: str = "") -> None:
        super().__init__(document)
        self.language = (language or "").lower()
        self.rules: list[tuple[QRegularExpression, QTextCharFormat]] = []
        self._build_rules()

    def _build_rules(self) -> None:
        keyword_format = QTextCharFormat()
        keyword_format.setForeground(QColor("#7CC7FF"))
        keyword_format.setFontWeight(QFont.Bold)

        string_format = QTextCharFormat()
        string_format.setForeground(QColor("#A8E6A2"))

        comment_format = QTextCharFormat()
        comment_format.setForeground(QColor("#8093A7"))
        comment_format.setFontItalic(True)

        if self.language == "diff":
            return

        for pattern in (
            r"\bclass\b",
            r"\bdef\b",
            r"\breturn\b",
            r"\bif\b",
            r"\belse\b",
            r"\belif\b",
            r"\bfor\b",
            r"\bwhile\b",
            r"\bimport\b",
            r"\bfrom\b",
            r"\basync\b",
            r"\bawait\b",
            r"\btry\b",
            r"\bexcept\b",
            r"\bconst\b",
            r"\blet\b",
            r"\bfunction\b",
        ):
            self.rules.append((QRegularExpression(pattern), keyword_format))
        self.rules.append((QRegularExpression(r"\".*?\""), string_format))
        self.rules.append((QRegularExpression(r"'.*?'"), string_format))
        self.rules.append((QRegularExpression(r"#.*$"), comment_format))
        self.rules.append((QRegularExpression(r"//.*$"), comment_format))

    def highlightBlock(self, text: str) -> None:
        if self.language == "diff":
            text_format = QTextCharFormat()
            line_bg = None
            marker_match = re.match(r"^\s*\d*\s+\d*\s(?P<marker>[+\- ])\s", text)
            marker = marker_match.group("marker") if marker_match else ""

            if marker == "+":
                text_format.setForeground(QColor("#8FE388"))
                line_bg = QColor("#1E3425")
                text_format.setBackground(line_bg)
            elif marker == "-":
                text_format.setForeground(QColor("#FF8B8B"))
                line_bg = QColor("#472B2B")
                text_format.setBackground(line_bg)
            elif (
                text.lstrip().startswith("@@")
                or text.lstrip().startswith("diff --git")
                or text.lstrip().startswith("index ")
                or text.lstrip().startswith("--- ")
                or text.lstrip().startswith("+++ ")
            ):
                text_format.setForeground(QColor("#7CC7FF"))
                text_format.setFontWeight(QFont.Bold)
            if text_format.foreground().color().isValid():
                self.setFormat(0, len(text), text_format)

            if marker_match:
                number_format = QTextCharFormat()
                number_format.setForeground(QColor("#747C89"))
                if line_bg is not None:
                    number_format.setBackground(line_bg)
                self.setFormat(
                    0,
                    marker_match.start("marker"),
                    number_format,
                )
            return

        for expression, text_format in self.rules:
            iterator = expression.globalMatch(text)
            while iterator.hasNext():
                match = iterator.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), text_format)


class CodeBlockWidget(QWidget):
    def __init__(self, code: str, language: str = "", title: str = "") -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("TranscriptMeta")
        self.title_label.setVisible(bool(title))
        title_row.addWidget(self.title_label, 0, Qt.AlignVCenter)
        title_row.addStretch(1)

        self.copy_button = QToolButton()
        self.copy_button.setObjectName("CodeCopyButton")
        self.copy_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.copy_button.setCursor(Qt.PointingHandCursor)
        self.copy_button.setToolTip("Copy")
        self.copy_button.setIcon(_fa_icon("fa5s.copy", color=TEXT_MUTED, size=13))
        self.copy_button.clicked.connect(self._copy_code)
        title_row.addWidget(self.copy_button, 0, Qt.AlignVCenter)

        layout.addLayout(title_row)

        self.editor = QPlainTextEdit()
        self.editor.setObjectName("CodeView")
        self.editor.setReadOnly(True)
        self.editor.setFont(_make_mono_font())
        self.editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        
        self.highlighter = CodeHighlighter(self.editor.document(), language)
        layout.addWidget(self.editor)
        
        self.set_code(code, language, title)

    def set_code(self, code: str, language: str = "", title: str = "") -> None:
        if title:
            self.title_label.setText(title)
            self.title_label.setVisible(True)
        else:
            self.title_label.setVisible(False)

        if self.highlighter.language != language.lower():
            self.highlighter.setDocument(None)
            self.highlighter = CodeHighlighter(self.editor.document(), language)

        if self.editor.toPlainText() != code:
            self.editor.setPlainText(code)
        self._sync_height()

    def _sync_height(self) -> None:
        _sync_plain_text_height(self.editor, min_lines=2, max_lines=30, extra_padding=18)

    def _copy_code(self) -> None:
        QApplication.clipboard().setText(self.editor.toPlainText())
        self.copy_button.setText("Copied")
        self.copy_button.setIcon(_fa_icon("fa5s.check", color=SUCCESS_GREEN, size=13))
        QTimer.singleShot(1200, self._reset_copy_button)

    def _reset_copy_button(self) -> None:
        self.copy_button.setText("Copy")
        self.copy_button.setIcon(_fa_icon("fa5s.copy", color=TEXT_MUTED, size=13))


class DiffBlockWidget(QFrame):
    def __init__(self, diff_text: str, source_path: str = "") -> None:
        super().__init__()
        self.setObjectName("DiffPanel")
        self.setFrameShape(QFrame.NoFrame)
        self._raw_diff = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(10, 7, 10, 7)
        header_row.setSpacing(8)

        self.path_label = QLabel("diff")
        self.path_label.setObjectName("DiffHeaderPath")
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        header_row.addWidget(self.path_label, 1)

        self.added_label = QLabel("+0")
        self.added_label.setObjectName("DiffStatAdded")
        header_row.addWidget(self.added_label, 0, Qt.AlignVCenter)

        self.removed_label = QLabel("-0")
        self.removed_label.setObjectName("DiffStatRemoved")
        header_row.addWidget(self.removed_label, 0, Qt.AlignVCenter)

        self.copy_button = QToolButton()
        self.copy_button.setObjectName("CodeCopyButton")
        self.copy_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.copy_button.setCursor(Qt.PointingHandCursor)
        self.copy_button.setToolTip("Copy diff")
        self.copy_button.setIcon(_fa_icon("fa5s.copy", color=TEXT_MUTED, size=13))
        self.copy_button.clicked.connect(self._copy_diff)
        header_row.addWidget(self.copy_button, 0, Qt.AlignVCenter)
        layout.addLayout(header_row)

        self.editor = QPlainTextEdit()
        self.editor.setObjectName("DiffCodeView")
        self.editor.setReadOnly(True)
        self.editor.setFont(_make_mono_font())
        self.editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.editor.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.editor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.highlighter = CodeHighlighter(self.editor.document(), "diff")
        layout.addWidget(self.editor)

        self.set_diff(diff_text, source_path=source_path)

    def set_diff(self, diff_text: str, source_path: str = "") -> None:
        self._raw_diff = str(diff_text or "")
        rendered, added, removed = _render_diff_with_line_numbers(self._raw_diff)
        self.path_label.setText(_extract_diff_path(self._raw_diff, source_path))
        self.added_label.setText(f"+{added}")
        self.removed_label.setText(f"-{removed}")
        display_text = rendered if rendered else self._raw_diff
        if self.editor.toPlainText() != display_text:
            self.editor.setPlainText(display_text)
        _sync_plain_text_height(self.editor, min_lines=3, max_lines=26, extra_padding=18)

    def _copy_diff(self) -> None:
        QApplication.clipboard().setText(self._raw_diff)
        self.copy_button.setText("Copied")
        self.copy_button.setIcon(_fa_icon("fa5s.check", color=SUCCESS_GREEN, size=13))
        QTimer.singleShot(1200, self._reset_copy_button)

    def _reset_copy_button(self) -> None:
        self.copy_button.setText("")
        self.copy_button.setIcon(_fa_icon("fa5s.copy", color=TEXT_MUTED, size=13))


class CollapsibleSection(QFrame):
    def __init__(self, title: str, content: QWidget, expanded: bool = False, indent: int = 0) -> None:
        super().__init__()
        self.setObjectName("ToolExpandablePanel")
        self.setFrameShape(QFrame.NoFrame)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.toggle_button = QPushButton()
        self.toggle_button.setObjectName("ToolExpandableToggle")
        self.toggle_button.setText(title)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(expanded)
        self.toggle_button.setFlat(True)
        self.toggle_button.setCursor(Qt.PointingHandCursor)
        self.toggle_button.setMinimumHeight(20)
        self.toggle_button.setIconSize(QSize(8, 8))
        self._set_toggle_icon(expanded)

        self.content = content
        self.content_container = QWidget()
        self.content_container.setObjectName("ToolExpandableContent")
        self.content_container.setAttribute(Qt.WA_StyledBackground, True)
        content_layout = QHBoxLayout(self.content_container)
        content_layout.setContentsMargins(8 + indent, 0, 8, 8)
        content_layout.setSpacing(0)
        content_layout.addWidget(self.content, 1)
        self.content_container.setVisible(expanded)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.toggle_button)
        layout.addWidget(self.content_container)
        self.toggle_button.toggled.connect(self.set_expanded)

    def _set_toggle_icon(self, expanded: bool) -> None:
        icon_color = TEXT_PRIMARY if expanded else TEXT_MUTED
        self.toggle_button.setIcon(
            _fa_icon("fa5s.caret-down" if expanded else "fa5s.caret-right", color=icon_color, size=10)
        )

    def set_expanded(self, expanded: bool) -> None:
        if self.toggle_button.isChecked() != expanded:
            self.toggle_button.blockSignals(True)
            self.toggle_button.setChecked(expanded)
            self.toggle_button.blockSignals(False)
        self._set_toggle_icon(expanded)
        self.content_container.setVisible(expanded)


class ComposerTextEdit(QPlainTextEdit):
    submit_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ComposerEdit")
        self._history_by_session: dict[str, list[str]] = {}
        self._history_session_id = "__default__"
        self._history_nav_index = 0
        self._file_index: list[dict[str, Any]] = []
        self._file_index_root = ""
        self._mention_popup = _ComposerMentionPopup(self)
        self._mention_popup.file_selected.connect(self._insert_selected_mention)
        self.textChanged.connect(self._refresh_mention_popup)
        self.cursorPositionChanged.connect(self._refresh_mention_popup)
        self.set_history_session("")

    def set_history_session(self, session_id: str) -> None:
        self._history_session_id = session_id or "__default__"
        self._history_by_session.setdefault(self._history_session_id, [])
        self._reset_history_navigation()
        self._close_mention_popup()

    def sync_session_history_from_transcript(self, session_id: str, transcript_payload: dict[str, Any] | None) -> None:
        key = session_id or "__default__"
        payload = transcript_payload if isinstance(transcript_payload, dict) else {}
        turns = payload.get("turns", []) or []
        history: list[str] = []
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            user_text = str(turn.get("user_text", "") or "").strip()
            if not user_text:
                continue
            if not history or history[-1] != user_text:
                history.append(user_text)
        self._history_by_session[key] = history
        if key == self._history_session_id:
            self._reset_history_navigation()

    def append_submitted_message(self, text: str) -> None:
        value = str(text or "").strip()
        if not value:
            return
        history = self._history_for_session()
        if not history or history[-1] != value:
            history.append(value)
        self._reset_history_navigation()

    def clear_history_for_session(self, session_id: str | None = None) -> None:
        key = (session_id or self._history_session_id) or "__default__"
        self._history_by_session[key] = []
        if key == self._history_session_id:
            self._reset_history_navigation()

    def reset_history_navigation(self) -> None:
        self._reset_history_navigation()

    def format_file_reference(self, path_value: str | Path) -> str:
        path = Path(path_value)
        cwd = Path.cwd()
        try:
            normalized = str(path.relative_to(cwd).as_posix())
        except ValueError:
            normalized = str(path.as_posix())
        if " " in normalized:
            return f'"{normalized}"'
        return normalized

    def set_file_index_for_testing(self, rel_paths: list[str]) -> None:
        root = Path.cwd()
        self._file_index_root = str(root)
        self._file_index = [
            self._build_file_index_row(root, root / Path(value))
            for value in rel_paths
        ]
        self._file_index.sort(key=lambda row: row["relative"])

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._mention_popup.isVisible():
            key = event.key()
            if key == Qt.Key_Up:
                event.accept()
                self._mention_popup.move_selection(-1)
                return
            if key == Qt.Key_Down:
                event.accept()
                self._mention_popup.move_selection(1)
                return
            if key in {Qt.Key_Return, Qt.Key_Enter} and not (event.modifiers() & Qt.ShiftModifier):
                event.accept()
                self._accept_current_mention()
                return
            if key == Qt.Key_Escape:
                event.accept()
                self._close_mention_popup()
                return

        history = self._history_for_session()
        is_browsing_history = 0 <= self._history_nav_index < len(history)
        if event.key() in {Qt.Key_Up, Qt.Key_Down} and (not self.toPlainText() or is_browsing_history):
            if self._navigate_history(-1 if event.key() == Qt.Key_Up else 1):
                event.accept()
                return

        if event.key() in {Qt.Key_Return, Qt.Key_Enter} and not (event.modifiers() & Qt.ShiftModifier):
            event.accept()
            self.submit_requested.emit()
            return

        if self._should_reset_history_nav(event):
            self._reset_history_navigation()

        super().keyPressEvent(event)
        self._refresh_mention_popup()

    def insertFromMimeData(self, source: QMimeData) -> None:  # type: ignore[override]
        if source.hasText():
            self.insertPlainText(source.text())
        else:
            super().insertFromMimeData(source)
        self._refresh_mention_popup()

    def focusOutEvent(self, event) -> None:  # type: ignore[override]
        self._close_mention_popup()
        super().focusOutEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        super().mouseReleaseEvent(event)
        self._refresh_mention_popup()

    def _history_for_session(self) -> list[str]:
        return self._history_by_session.setdefault(self._history_session_id, [])

    def _should_reset_history_nav(self, event: QKeyEvent) -> bool:
        if event.text():
            return True
        return event.key() in {
            Qt.Key_Backspace,
            Qt.Key_Delete,
        }

    def _reset_history_navigation(self) -> None:
        self._history_nav_index = len(self._history_for_session())

    def _set_plain_text_and_move_cursor_to_end(self, value: str) -> None:
        self.setPlainText(value)
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)

    def _navigate_history(self, direction: int) -> bool:
        history = self._history_for_session()
        if not history:
            return False

        if direction < 0:
            if self._history_nav_index > 0:
                self._history_nav_index -= 1
            else:
                self._history_nav_index = 0
        else:
            if self._history_nav_index < len(history) - 1:
                self._history_nav_index += 1
            else:
                self._history_nav_index = len(history)
                self._set_plain_text_and_move_cursor_to_end("")
                return True

        if 0 <= self._history_nav_index < len(history):
            self._set_plain_text_and_move_cursor_to_end(history[self._history_nav_index])
            return True
        return False

    def _refresh_mention_popup(self) -> None:
        mention = self._current_mention_token()
        if mention is None:
            self._close_mention_popup()
            return

        query = mention["query"].lower()
        matches = self._filter_mention_candidates(query)
        if not matches:
            self._close_mention_popup()
            return

        self._mention_popup.set_items(matches)
        self._position_mention_popup()
        if not self._mention_popup.isVisible():
            self._mention_popup.show()
        self._mention_popup.raise_()

    def _current_mention_token(self) -> dict[str, int | str] | None:
        text = self.toPlainText()
        cursor = self.textCursor()
        pos = cursor.position()
        if pos < 0:
            return None

        before_cursor = text[:pos]
        match = re.search(r"(?:^|\s)@([^\s@]*)$", before_cursor)
        if not match:
            return None

        token_start = match.start(1) - 1
        if token_start < 0:
            return None

        return {
            "start": token_start,
            "end": pos,
            "query": match.group(1),
        }

    def _ensure_file_index(self) -> None:
        root = Path.cwd()
        root_str = str(root)
        if self._file_index_root == root_str:
            return

        rows: list[dict[str, Any]] = []
        for current_root, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in COMPOSER_MENTION_EXCLUDED_DIRS]
            for filename in filenames:
                full_path = Path(current_root) / filename
                rows.append(self._build_file_index_row(root, full_path))
        rows.sort(key=lambda row: row["relative"])
        self._file_index = rows
        self._file_index_root = root_str

    def _build_file_index_row(self, root: Path, full_path: Path) -> dict[str, Any]:
        try:
            relative = full_path.relative_to(root).as_posix()
        except ValueError:
            relative = full_path.as_posix()
        folder = Path(relative).parent.as_posix()
        if folder == ".":
            folder = ""
        return {
            "name": full_path.name,
            "name_lower": full_path.name.lower(),
            "relative": relative,
            "relative_lower": relative.lower(),
            "folder": folder,
            "depth": relative.count("/"),
        }

    def _filter_mention_candidates(self, query_lower: str) -> list[dict[str, Any]]:
        self._ensure_file_index()
        if not self._file_index:
            return []

        if not query_lower:
            return sorted(
                self._file_index,
                key=lambda row: (int(row.get("depth", 0)), len(str(row["relative"])), str(row["relative"])),
            )[:COMPOSER_MENTION_MAX_ITEMS]

        ranked: list[tuple[int, int, int, dict[str, str]]] = []
        for row in self._file_index:
            name_lower = row["name_lower"]
            relative_lower = row["relative_lower"]
            if query_lower not in name_lower and query_lower not in relative_lower:
                continue

            if name_lower.startswith(query_lower):
                rank = 0
            elif query_lower in name_lower:
                rank = 1
            elif relative_lower.startswith(query_lower):
                rank = 2
            else:
                rank = 3
            ranked.append((rank, int(row.get("depth", 0)), len(row["relative"]), row))

        ranked.sort(key=lambda item: (item[0], item[1], item[2], item[3]["relative"]))
        return [item[3] for item in ranked[:COMPOSER_MENTION_MAX_ITEMS]]

    def _position_mention_popup(self) -> None:
        anchor = self.cursorRect().bottomLeft() + QPoint(0, 6)
        global_pos = self.mapToGlobal(anchor)
        popup_size = self._mention_popup.sizeHint()
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            bounds = screen.availableGeometry()
            max_x = max(bounds.left() + 8, bounds.right() - popup_size.width() - 8)
            max_y = max(bounds.top() + 8, bounds.bottom() - popup_size.height() - 8)
            global_pos.setX(max(bounds.left() + 8, min(global_pos.x(), max_x)))
            global_pos.setY(max(bounds.top() + 8, min(global_pos.y(), max_y)))
        self._mention_popup.move(global_pos)

    def _accept_current_mention(self) -> None:
        selected = self._mention_popup.current_relative_path()
        if not selected:
            self._close_mention_popup()
            return
        self._insert_selected_mention(selected)

    def _insert_selected_mention(self, relative_path: str) -> None:
        mention = self._current_mention_token()
        if mention is None:
            self._close_mention_popup()
            return

        replacement = self.format_file_reference(relative_path)
        cursor = self.textCursor()
        cursor.setPosition(int(mention["start"]))
        cursor.setPosition(int(mention["end"]), QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(replacement)
        self.setTextCursor(cursor)
        self.setFocus()
        self._close_mention_popup()

    def _close_mention_popup(self) -> None:
        self._mention_popup.hide()


class _ComposerMentionPopup(QFrame):
    file_selected = Signal(str)

    def __init__(self, owner: QWidget) -> None:
        window_flags = Qt.Tool | Qt.FramelessWindowHint
        if hasattr(Qt, "WindowDoesNotAcceptFocus"):
            window_flags |= Qt.WindowDoesNotAcceptFocus
        super().__init__(None, window_flags)
        self._owner = owner
        self.setObjectName("ComposerMentionPopup")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFrameShape(QFrame.NoFrame)
        self.setMinimumWidth(COMPOSER_MENTION_POPUP_MIN_WIDTH)
        self.setMaximumWidth(COMPOSER_MENTION_POPUP_MAX_WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("ComposerMentionList")
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.list_widget.setTextElideMode(Qt.ElideMiddle)
        self.list_widget.setSelectionMode(QListWidget.SingleSelection)
        self.list_widget.setFocusPolicy(Qt.NoFocus)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        self.list_widget.itemActivated.connect(self._on_item_clicked)
        layout.addWidget(self.list_widget)

    def set_items(self, rows: list[dict[str, Any]]) -> None:
        self.list_widget.clear()
        max_text_width = 0
        metrics = self.list_widget.fontMetrics()
        for row in rows:
            relative = str(row["relative"])
            text = relative
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, relative)
            item.setToolTip(relative)
            self.list_widget.addItem(item)
            max_text_width = max(max_text_width, metrics.horizontalAdvance(text))

        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)
        row_height = self.list_widget.sizeHintForRow(0) if self.list_widget.count() > 0 else 22
        visible_count = min(8, max(1, self.list_widget.count()))
        total_height = visible_count * max(20, row_height) + 8
        self.list_widget.setFixedHeight(total_height)
        self.setFixedHeight(total_height + 8)
        target_width = max_text_width + 70
        popup_width = max(COMPOSER_MENTION_POPUP_MIN_WIDTH, min(COMPOSER_MENTION_POPUP_MAX_WIDTH, target_width))
        self.setFixedWidth(popup_width)

    def move_selection(self, delta: int) -> None:
        count = self.list_widget.count()
        if count <= 0:
            return
        current = self.list_widget.currentRow()
        if current < 0:
            current = 0
        target = max(0, min(count - 1, current + delta))
        self.list_widget.setCurrentRow(target)

    def current_relative_path(self) -> str:
        item = self.list_widget.currentItem()
        if item is None:
            return ""
        return str(item.data(Qt.UserRole) or "")

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        relative = str(item.data(Qt.UserRole) or "")
        if relative:
            self.file_selected.emit(relative)


def _format_sidebar_time(value: str) -> str:
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return value
    local_dt = dt.astimezone()
    now = datetime.now(local_dt.tzinfo or timezone.utc)
    delta = now - local_dt
    if delta.total_seconds() < 0:
        return "сейчас"
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "сейчас"
    if minutes < 60:
        return f"{minutes}м"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}ч"
    days = hours // 24
    if days < 7:
        return f"{days}д"
    weeks = days // 7
    if weeks < 5:
        return f"{weeks}н"
    return local_dt.strftime("%d %b")


def _sidebar_project_name(project_path: str) -> str:
    text = str(project_path or "").replace("\\", "/").rstrip("/")
    if not text:
        return "project"
    return text.split("/")[-1] or text


def _sidebar_dt(value: str) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class SessionListModel(QAbstractListModel):
    KindRole = Qt.UserRole + 1
    SessionIdRole = Qt.UserRole + 2
    TitleRole = Qt.UserRole + 3
    UpdatedAtRole = Qt.UserRole + 4
    ProjectPathRole = Qt.UserRole + 5
    ProjectTitleRole = Qt.UserRole + 6

    def __init__(self) -> None:
        super().__init__()
        self._items: list[dict[str, str]] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # type: ignore[override]
        if parent.isValid():
            return 0
        return len(self._items)

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:  # type: ignore[override]
        if not index.isValid():
            return Qt.NoItemFlags
        kind = str(self.data(index, self.KindRole) or "session")
        if kind == "group":
            return Qt.ItemIsEnabled
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:  # type: ignore[override]
        if not index.isValid() or not (0 <= index.row() < len(self._items)):
            return None
        item = self._items[index.row()]
        if role == self.KindRole:
            return item.get("kind", "session")
        if role == Qt.DisplayRole:
            return item.get("title", "")
        if role == self.SessionIdRole:
            return item.get("session_id", "")
        if role == self.TitleRole:
            return item.get("title", "")
        if role == self.UpdatedAtRole:
            return item.get("updated_at", "")
        if role == self.ProjectPathRole:
            return item.get("project_path", "")
        if role == self.ProjectTitleRole:
            return item.get("project_title", "")
        return None

    def set_sessions(self, sessions: list[dict[str, str]]) -> None:
        grouped: dict[str, list[dict[str, str]]] = {}
        for raw in sessions:
            row = dict(raw)
            project_key = str(row.get("project_path", "")).strip()
            grouped.setdefault(project_key, []).append(row)

        project_rows: list[tuple[str, list[dict[str, str]]]] = sorted(
            grouped.items(),
            key=lambda pair: max((_sidebar_dt(item.get("updated_at", "")) for item in pair[1]), default=datetime.fromtimestamp(0, tz=timezone.utc)),
            reverse=True,
        )

        items: list[dict[str, str]] = []
        for project_path, rows in project_rows:
            items.append(
                {
                    "kind": "group",
                    "project_path": project_path,
                    "project_title": _sidebar_project_name(project_path),
                    "title": _sidebar_project_name(project_path),
                    "session_id": "",
                    "updated_at": "",
                }
            )
            sorted_rows = sorted(
                rows,
                key=lambda item: _sidebar_dt(item.get("updated_at", "")),
                reverse=True,
            )
            for row in sorted_rows:
                entry = dict(row)
                entry["kind"] = "session"
                entry["project_title"] = _sidebar_project_name(project_path)
                items.append(entry)

        self.beginResetModel()
        self._items = items
        self.endResetModel()

    def session_id_at(self, index: QModelIndex) -> str:
        if not index.isValid():
            return ""
        if str(self.data(index, self.KindRole) or "session") != "session":
            return ""
        return str(self.data(index, self.SessionIdRole) or "")

    def index_for_session(self, session_id: str) -> QModelIndex:
        if not session_id:
            return QModelIndex()
        for row, item in enumerate(self._items):
            if item.get("kind") == "session" and item.get("session_id") == session_id:
                return self.index(row, 0)
        return QModelIndex()

    def session_row_count(self) -> int:
        return sum(1 for item in self._items if item.get("kind") == "session")

    def title_for_session(self, session_id: str) -> str:
        if not session_id:
            return ""
        for item in self._items:
            if item.get("kind") == "session" and item.get("session_id") == session_id:
                return str(item.get("title", "") or "")
        return ""


class SessionItemDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:  # type: ignore[override]
        painter.save()
        item_kind = str(index.data(SessionListModel.KindRole) or "session")
        rect = option.rect.adjusted(6, 2, -6, -2)
        is_selected = bool(option.state & QStyle.State_Selected)
        is_hovered = bool(option.state & QStyle.State_MouseOver)

        if item_kind == "group":
            icon_rect = QRect(rect.left() + 2, rect.top() + 4, 14, 14)
            painter.drawPixmap(icon_rect, qta.icon("fa5.folder-open", color=TEXT_MUTED).pixmap(12, 12))
            group_text = str(index.data(SessionListModel.ProjectTitleRole) or index.data(SessionListModel.TitleRole) or "")
            title_rect = QRect(rect.left() + 20, rect.top(), rect.width() - 24, rect.height())
            title_font = option.font
            title_font.setPointSize(10)
            title_font.setWeight(QFont.DemiBold)
            painter.setFont(title_font)
            painter.setPen(QColor(TEXT_MUTED))
            painter.drawText(title_rect, Qt.AlignLeft | Qt.AlignVCenter, group_text)
            painter.restore()
            return

        background = QColor(0, 0, 0, 0)
        if is_selected:
            background = QColor(SURFACE_ALT)
        elif is_hovered:
            background = QColor(SURFACE_ALT)
            background.setAlpha(120)
        if background.alpha() > 0:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(Qt.NoPen)
            painter.setBrush(background)
            painter.drawRoundedRect(rect, 9, 9)

        title = str(index.data(SessionListModel.TitleRole) or "")
        updated_at = _format_sidebar_time(str(index.data(SessionListModel.UpdatedAtRole) or ""))
        title_font = option.font
        title_font.setPointSize(10.5)
        title_font.setWeight(QFont.DemiBold if is_selected else QFont.Medium)
        painter.setFont(title_font)
        painter.setPen(QColor(TEXT_PRIMARY))

        time_font = option.font
        time_font.setPointSize(9)
        time_font.setWeight(QFont.Medium)
        time_metrics = QFontMetrics(time_font)
        time_width = max(36, time_metrics.horizontalAdvance(updated_at) + 6)

        title_rect = QRect(rect.left() + 12, rect.top() + 1, rect.width() - 20 - time_width, rect.height() - 2)
        time_rect = QRect(rect.right() - time_width - 8, rect.top() + 1, time_width, rect.height() - 2)
        metrics = QFontMetrics(title_font)
        painter.drawText(
            title_rect,
            Qt.AlignLeft | Qt.AlignVCenter,
            metrics.elidedText(title, Qt.ElideRight, max(10, title_rect.width())),
        )

        painter.setFont(time_font)
        painter.setPen(QColor(TEXT_MUTED))
        painter.drawText(time_rect, Qt.AlignRight | Qt.AlignVCenter, updated_at)
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # type: ignore[override]
        _ = option
        kind = str(index.data(SessionListModel.KindRole) or "session")
        return QSize(240, 26 if kind == "group" else 36)


class SessionSidebarWidget(QWidget):
    session_activated = Signal(str)
    session_delete_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        title = QLabel("Беседы")
        title.setObjectName("SidebarSectionTitle")
        root.addWidget(title)

        self.list_view = QListView()
        self.list_view.setObjectName("SessionListView")
        self.list_view.setMouseTracking(True)
        self.list_view.setUniformItemSizes(False)
        self.list_view.setEditTriggers(QListView.NoEditTriggers)
        self.list_view.setSelectionMode(QListView.SingleSelection)
        self.list_view.setSelectionBehavior(QListView.SelectRows)
        self.list_view.setVerticalScrollMode(QListView.ScrollPerPixel)
        self.list_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_view.setContextMenuPolicy(Qt.CustomContextMenu)

        self.model = SessionListModel()
        self.delegate = SessionItemDelegate()
        self.list_view.setModel(self.model)
        self.list_view.setItemDelegate(self.delegate)
        self.list_view.clicked.connect(self._emit_clicked_session)
        self.list_view.customContextMenuRequested.connect(self._show_context_menu)
        root.addWidget(self.list_view, 1)

    def set_sessions(self, sessions: list[dict[str, str]], active_session_id: str) -> None:
        self.model.set_sessions(sessions)
        if not active_session_id:
            self.list_view.clearSelection()
            return
        index = self.model.index_for_session(active_session_id)
        if index.isValid():
            self.list_view.setCurrentIndex(index)
            self.list_view.scrollTo(index)
            return
        self.list_view.clearSelection()

    def _emit_clicked_session(self, index: QModelIndex) -> None:
        session_id = self.model.session_id_at(index)
        if session_id:
            self.session_activated.emit(session_id)

    def title_for_session(self, session_id: str) -> str:
        return self.model.title_for_session(session_id)

    def _show_context_menu(self, pos: QPoint) -> None:
        index = self.list_view.indexAt(pos)
        if not index.isValid():
            return
        if str(index.data(SessionListModel.KindRole) or "session") != "session":
            return

        session_id = self.model.session_id_at(index)
        if not session_id:
            return

        menu = QMenu(self.list_view)
        delete_action = menu.addAction("Удалить чат")
        selected = menu.exec(self.list_view.viewport().mapToGlobal(pos))
        if selected is delete_action:
            self.session_delete_requested.emit(session_id)


class OverviewPanelWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        frame = QFrame()
        frame.setObjectName("SidebarCard")
        form = QFormLayout(frame)
        form.setContentsMargins(12, 12, 12, 12)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)
        self._labels: dict[str, QLabel] = {}
        for key in (
            "Provider",
            "Model",
            "Backend",
            "Tools",
            "Session",
            "Thread",
            "Approvals",
            "MCP",
            "Status",
            "Config",
        ):
            label = QLabel("—")
            label.setWordWrap(True)
            self._labels[key] = label
            form.addRow(key, label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(frame)
        layout.addStretch(1)

    def set_snapshot(self, snapshot: dict[str, Any]) -> None:
        mapping = {
            "Provider": snapshot.get("provider", "—"),
            "Model": snapshot.get("model", "—"),
            "Backend": snapshot.get("backend", "—"),
            "Tools": str(snapshot.get("tools_count", "—")),
            "Session": snapshot.get("session_short", "—"),
            "Thread": snapshot.get("thread_short", "—"),
            "Approvals": snapshot.get("approvals", "—"),
            "MCP": snapshot.get("mcp_text", "—"),
            "Status": snapshot.get("status", "—"),
            "Config": snapshot.get("config_mode", "—"),
        }
        for key, value in mapping.items():
            self._labels[key].setText(str(value))


class ToolsPanelWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._container = QWidget()
        self._container.setObjectName("ToolsContainer")
        self._inner = QVBoxLayout(self._container)
        self._inner.setContentsMargins(6, 6, 6, 6)
        self._inner.setSpacing(6)
        self._inner.addStretch(1)

        self.scroll.setWidget(self._container)
        root.addWidget(self.scroll)

    def set_tools(self, tools: list[dict[str, str]]) -> None:
        while self._inner.count() > 1:
            item = self._inner.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        grouped: dict[str, list[dict[str, str]]] = {"Read-only": [], "Protected": [], "MCP": []}
        for row in tools:
            grouped.setdefault(row["group"], []).append(row)

        group_colors = {
            "Read-only": TEXT_MUTED,
            "Protected": AMBER_WARNING,
            "MCP": ACCENT_BLUE,
        }

        insert_pos = 0
        for group_name in ("Read-only", "Protected", "MCP"):
            items = grouped.get(group_name, [])
            if not items:
                continue

            header = QLabel(group_name.upper())
            header.setStyleSheet(
                f"color: {group_colors[group_name]}; font-size: 7.2pt; "
                f"font-weight: 700; letter-spacing: 0.8px; "
                f"padding: 8px 4px 3px 4px;"
            )
            self._inner.insertWidget(insert_pos, header)
            insert_pos += 1

            for row in items:
                card = QFrame()
                card.setObjectName("ToolCard")
                card.setStyleSheet(
                    f"QFrame#ToolCard {{ background: {SURFACE_CARD}; "
                    f"border: 1px solid {BORDER}; border-radius: 6px; "
                    f"margin: 1px 0px; }}"
                )
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(8, 6, 8, 6)
                card_layout.setSpacing(3)

                top_row = QHBoxLayout()
                top_row.setSpacing(6)

                name_label = QLabel(row["name"])
                name_label.setStyleSheet(
                    f"color: {TEXT_PRIMARY}; font-weight: 600; "
                    f"font-size: 8.8pt; font-family: 'Cascadia Mono';"
                )
                top_row.addWidget(name_label, 1)

                flags = row.get("flags", "")
                if flags:
                    for flag in flags.split(", "):
                        flag = flag.strip()
                        if not flag:
                            continue
                        flag_color = (
                            AMBER_WARNING if flag in ("mutating", "destructive", "approval")
                            else ACCENT_BLUE if flag in ("mcp", "network")
                            else TEXT_MUTED
                        )
                        chip = QLabel(flag)
                        chip.setStyleSheet(
                            f"color: {flag_color}; font-size: 7pt; "
                            f"border: 1px solid {flag_color}33; "
                            f"border-radius: 3px; padding: 0px 4px;"
                        )
                        top_row.addWidget(chip, 0)

                card_layout.addLayout(top_row)

                desc = row.get("description", "")
                if desc:
                    desc_label = QLabel(desc)
                    desc_label.setWordWrap(True)
                    desc_label.setStyleSheet(
                        f"color: {TEXT_MUTED}; font-size: 8pt;"
                    )
                    card_layout.addWidget(desc_label)

                self._inner.insertWidget(insert_pos, card)
                insert_pos += 1

            sep = QFrame()
            sep.setFixedHeight(1)
            sep.setStyleSheet(f"background: {BORDER}; margin: 4px 0px;")
            self._inner.insertWidget(insert_pos, sep)
            insert_pos += 1


class ModelSettingsDialog(QDialog):
    def __init__(self, payload: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ModelSettingsDialog")
        self.setWindowTitle("Model Settings")
        self.setModal(True)
        self.resize(820, 500)
        self.setMinimumSize(760, 430)

        normalized = normalize_profiles_payload(payload or {})
        self._profiles: list[dict[str, str]] = [dict(item) for item in normalized.get("profiles", [])]
        self._active_profile = str(normalized.get("active_profile") or "").strip()
        self._name_manual_flags: list[bool] = [bool(str(item.get("id") or "").strip()) for item in self._profiles]
        self._selected_row = -1
        self._loading_form = False
        self._result_payload = normalized

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        header_title = QLabel("Model Profiles")
        header_title.setObjectName("ModelSettingsTitle")
        root.addWidget(header_title)

        header_hint = QLabel("Manage provider/model/API credentials. Active profile is used for new runs.")
        header_hint.setObjectName("ModelSettingsSubtitle")
        header_hint.setWordWrap(True)
        root.addWidget(header_hint)

        body = QHBoxLayout()
        body.setSpacing(12)

        left_container = QFrame()
        left_container.setObjectName("ModelSettingsPane")
        left = QVBoxLayout(left_container)
        left.setContentsMargins(10, 10, 10, 10)
        left.setSpacing(6)
        left_label = QLabel("Profiles")
        left_label.setObjectName("SectionTitle")
        left.addWidget(left_label)

        self.profile_list = QListWidget()
        self.profile_list.setObjectName("ModelProfileList")
        self.profile_list.setMinimumWidth(220)
        self.profile_list.currentRowChanged.connect(self._on_selection_changed)
        left.addWidget(self.profile_list, 1)

        left_buttons = QHBoxLayout()
        left_buttons.setSpacing(6)
        self.add_button = QPushButton("Add")
        self.add_button.setObjectName("SettingsAddButton")
        self.add_button.setIcon(_fa_icon("fa5s.plus", color=TEXT_PRIMARY, size=11))
        self.delete_button = QPushButton("Delete")
        self.delete_button.setObjectName("SettingsDeleteButton")
        self.delete_button.setIcon(_fa_icon("fa5s.trash", color=TEXT_PRIMARY, size=11))
        left_buttons.addWidget(self.add_button)
        left_buttons.addWidget(self.delete_button)
        left.addLayout(left_buttons)

        left_hint = QLabel("Tip: leave Name empty to auto-generate it from Model.")
        left_hint.setObjectName("ModelSettingsMeta")
        left_hint.setWordWrap(True)
        left.addWidget(left_hint)

        right_container = QFrame()
        right_container.setObjectName("ModelSettingsPane")
        right = QVBoxLayout(right_container)
        right.setContentsMargins(10, 10, 10, 10)
        right.setSpacing(8)
        right_label = QLabel("Profile")
        right_label.setObjectName("SectionTitle")
        right.addWidget(right_label)

        self.form_hint = QLabel("Select a profile and edit fields on the right.")
        self.form_hint.setObjectName("ModelSettingsMeta")
        self.form_hint.setWordWrap(True)
        right.addWidget(self.form_hint)

        form_frame = QFrame()
        form_frame.setObjectName("ModelSettingsFormCard")
        form_layout = QFormLayout(form_frame)
        form_layout.setContentsMargins(10, 10, 10, 10)
        form_layout.setHorizontalSpacing(10)
        form_layout.setVerticalSpacing(8)
        form_layout.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form_layout.setFormAlignment(Qt.AlignTop)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("profile-id")
        self.provider_combo = QComboBox()
        self.provider_combo.addItems(["openai", "gemini"])
        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("e.g. openai/gpt-oss-120b or gemini-1.5-flash")
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText("API key")
        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText("https://api.openai.com/v1")

        label_width = 76
        name_label = QLabel("Name")
        provider_label = QLabel("Provider")
        model_label = QLabel("Model")
        api_key_label = QLabel("API Key")
        base_url_label = QLabel("Base URL")
        for label in (name_label, provider_label, model_label, api_key_label, base_url_label):
            label.setObjectName("ModelSettingsFieldLabel")
            label.setFixedWidth(label_width)

        form_layout.addRow(name_label, self.name_edit)
        form_layout.addRow(provider_label, self.provider_combo)
        form_layout.addRow(model_label, self.model_edit)
        form_layout.addRow(api_key_label, self.api_key_edit)
        form_layout.addRow(base_url_label, self.base_url_edit)
        right.addWidget(form_frame, 1)

        body.addWidget(left_container, 0)
        body.addWidget(right_container, 1)
        root.addLayout(body, 1)

        actions = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        actions.setObjectName("ModelSettingsActions")
        self.save_button = actions.button(QDialogButtonBox.StandardButton.Save)
        if self.save_button is not None:
            self.save_button.setObjectName("PrimaryButton")
            self.save_button.setIcon(_fa_icon("fa5s.save", color="#FFFFFF", size=11))
            self.save_button.setMinimumHeight(30)
        self.cancel_button = actions.button(QDialogButtonBox.StandardButton.Cancel)
        if self.cancel_button is not None:
            self.cancel_button.setMinimumHeight(30)
        root.addWidget(actions)

        self.add_button.clicked.connect(self._add_profile)
        self.delete_button.clicked.connect(self._delete_selected_profile)
        actions.accepted.connect(self._save_and_accept)
        actions.rejected.connect(self.reject)

        self.name_edit.textEdited.connect(self._on_name_edited)
        self.provider_combo.currentTextChanged.connect(self._on_provider_changed)
        self.model_edit.textChanged.connect(self._on_model_changed)
        self.api_key_edit.textChanged.connect(self._on_form_changed)
        self.base_url_edit.textChanged.connect(self._on_form_changed)

        self._refresh_profile_list()
        if self.profile_list.count() > 0:
            self.profile_list.setCurrentRow(0)
        else:
            self._set_form_enabled(False)

    def result_payload(self) -> dict[str, Any]:
        return dict(self._result_payload)

    def _current_row(self) -> int:
        return self.profile_list.currentRow()

    def _set_form_enabled(self, enabled: bool) -> None:
        for widget in (
            self.name_edit,
            self.provider_combo,
            self.model_edit,
            self.api_key_edit,
            self.base_url_edit,
        ):
            widget.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)
        if enabled:
            self._update_base_url_field_state(self.provider_combo.currentText())

    def _update_base_url_field_state(self, provider: str) -> None:
        provider_normalized = str(provider or "").strip().lower()
        enabled = provider_normalized == "openai"
        self.base_url_edit.setEnabled(enabled)
        if enabled:
            self.base_url_edit.setPlaceholderText("https://api.openai.com/v1")
            self.base_url_edit.setToolTip("")
        else:
            self.base_url_edit.setPlaceholderText("Not used for gemini")
            self.base_url_edit.setToolTip("Base URL is only used for openai profiles.")

    def _display_name(self, profile: dict[str, str]) -> str:
        profile_id = str(profile.get("id") or "").strip()
        provider = str(profile.get("provider") or "").strip()
        model_name = str(profile.get("model") or "").strip()
        marker = " • active" if profile_id and profile_id == self._active_profile else ""
        title = profile_id if profile_id else "(unnamed)"
        if marker:
            title = f"{title}{marker}"
        details = " · ".join(part for part in (provider, model_name) if part)
        return f"{title}\n{details}" if details else title

    def _build_profile_item_widget(self, profile: dict[str, str]) -> QWidget:
        container = QWidget()
        container.setObjectName("ModelProfileItemWidget")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(2)

        first_row = QHBoxLayout()
        first_row.setContentsMargins(0, 0, 0, 0)
        first_row.setSpacing(4)

        profile_id = str(profile.get("id") or "").strip() or "(unnamed)"
        is_active = bool(profile_id and profile_id != "(unnamed)" and profile_id == self._active_profile)
        title_label = QLabel(profile_id)
        title_label.setObjectName("ModelProfileItemTitle")
        first_row.addWidget(title_label, 0, Qt.AlignLeft | Qt.AlignVCenter)

        if is_active:
            active_label = QLabel("• active")
            active_label.setObjectName("ModelProfileItemActive")
            first_row.addWidget(active_label, 0, Qt.AlignLeft | Qt.AlignVCenter)

        first_row.addStretch(1)
        layout.addLayout(first_row)

        provider = str(profile.get("provider") or "").strip()
        model_name = str(profile.get("model") or "").strip()
        details = " · ".join(part for part in (provider, model_name) if part)
        details_label = QLabel(details)
        details_label.setObjectName("ModelProfileItemMeta")
        details_label.setWordWrap(False)
        layout.addWidget(details_label, 0, Qt.AlignLeft | Qt.AlignVCenter)
        container.adjustSize()
        return container

    def _refresh_profile_list(self, preferred_row: int | None = None) -> None:
        self.profile_list.blockSignals(True)
        self.profile_list.clear()
        for profile in self._profiles:
            item_widget = self._build_profile_item_widget(profile)
            item = QListWidgetItem("")
            item.setData(Qt.UserRole, self._display_name(profile))
            widget_hint = item_widget.sizeHint()
            item_height = max(48, widget_hint.height() + 4)
            item.setSizeHint(QSize(widget_hint.width(), item_height))
            provider = str(profile.get("provider") or "").strip()
            model_name = str(profile.get("model") or "").strip()
            item.setToolTip(f"Provider: {provider}\nModel: {model_name}".strip())
            self.profile_list.addItem(item)
            self.profile_list.setItemWidget(item, item_widget)
        self.profile_list.blockSignals(False)
        if self.save_button is not None:
            self.save_button.setEnabled(bool(self._profiles))

        if not self._profiles:
            self._selected_row = -1
            self._set_form_enabled(False)
            self.form_hint.setText("Add a profile to start configuring models.")
            return

        row = preferred_row if preferred_row is not None else self._current_row()
        row = max(0, min(row, len(self._profiles) - 1))
        self.profile_list.setCurrentRow(row)

    def _sync_form_to_profile(self, row: int) -> None:
        if row < 0 or row >= len(self._profiles):
            self._selected_row = -1
            self._set_form_enabled(False)
            self.form_hint.setText("Select a profile and edit fields on the right.")
            return
        profile = self._profiles[row]
        self._loading_form = True
        self._set_form_enabled(True)
        self.name_edit.setText(str(profile.get("id", "")))
        provider = str(profile.get("provider", "openai")).strip().lower()
        if provider not in ALLOWED_PROVIDERS:
            provider = "openai"
        self.provider_combo.setCurrentText(provider)
        self.model_edit.setText(str(profile.get("model", "")))
        self.api_key_edit.setText(str(profile.get("api_key", "")))
        self.base_url_edit.setText(str(profile.get("base_url", "")))
        self._update_base_url_field_state(provider)
        self._loading_form = False
        self._selected_row = row
        profile_id = str(profile.get("id") or "").strip() or "(unnamed)"
        self.form_hint.setText(f"Editing profile: {profile_id}")

    def _sync_current_profile_from_form(self, row: int | None = None) -> None:
        if self._loading_form:
            return
        target_row = self._current_row() if row is None else row
        if target_row < 0 or target_row >= len(self._profiles):
            return
        provider = str(self.provider_combo.currentText() or "").strip().lower()
        if provider not in ALLOWED_PROVIDERS:
            provider = "openai"
        base_url = str(self.base_url_edit.text() or "").strip() if provider == "openai" else ""
        self._profiles[target_row] = {
            "id": str(self.name_edit.text() or "").strip(),
            "provider": provider,
            "model": str(self.model_edit.text() or "").strip(),
            "api_key": str(self.api_key_edit.text() or "").strip(),
            "base_url": base_url,
        }
        item = self.profile_list.item(target_row)
        if item is not None:
            item.setText(self._display_name(self._profiles[target_row]))

    def _suggest_unique_id(self, model_text: str, *, row: int) -> str:
        used = {
            str(profile.get("id") or "").strip()
            for idx, profile in enumerate(self._profiles)
            if idx != row and str(profile.get("id") or "").strip()
        }
        return generate_profile_id(model_text, used)

    def _on_selection_changed(self, row: int) -> None:
        previous_row = self._selected_row
        if previous_row != row:
            self._sync_current_profile_from_form(previous_row)
        self._sync_form_to_profile(row)

    def _on_name_edited(self, text: str) -> None:
        row = self._current_row()
        if 0 <= row < len(self._name_manual_flags):
            self._name_manual_flags[row] = bool(str(text or "").strip())
        self._sync_current_profile_from_form()

    def _on_model_changed(self, _text: str) -> None:
        if self._loading_form:
            return
        row = self._current_row()
        if row < 0 or row >= len(self._profiles):
            return
        current_name = str(self.name_edit.text() or "").strip()
        if (not self._name_manual_flags[row]) or not current_name:
            self._loading_form = True
            self.name_edit.setText(self._suggest_unique_id(self.model_edit.text(), row=row))
            self._loading_form = False
        self._on_form_changed()

    def _on_form_changed(self) -> None:
        self._sync_current_profile_from_form()

    def _on_provider_changed(self, provider: str) -> None:
        if self._loading_form:
            return
        self._update_base_url_field_state(provider)
        if str(provider or "").strip().lower() != "openai":
            self._loading_form = True
            self.base_url_edit.clear()
            self._loading_form = False
        self._on_form_changed()

    def _add_profile(self) -> None:
        self._sync_current_profile_from_form(self._selected_row)
        self._profiles.append(
            {
                "id": "",
                "provider": "openai",
                "model": "",
                "api_key": "",
                "base_url": "",
            }
        )
        self._name_manual_flags.append(False)
        self._refresh_profile_list(preferred_row=len(self._profiles) - 1)

    def _delete_selected_profile(self) -> None:
        row = self._current_row()
        if row < 0 or row >= len(self._profiles):
            return
        self._sync_current_profile_from_form(self._selected_row)
        removed_id = str(self._profiles[row].get("id") or "").strip()
        self._profiles.pop(row)
        self._name_manual_flags.pop(row)

        if removed_id and removed_id == self._active_profile:
            self._active_profile = str(self._profiles[0].get("id") or "").strip() if self._profiles else ""

        self._refresh_profile_list(preferred_row=row)

    def _validated_payload(self) -> dict[str, Any] | None:
        self._sync_current_profile_from_form(self._selected_row)
        profiles: list[dict[str, str]] = []
        used_ids: set[str] = set()

        for idx, profile in enumerate(self._profiles):
            provider = str(profile.get("provider") or "").strip().lower()
            model_name = str(profile.get("model") or "").strip()
            if provider not in ALLOWED_PROVIDERS:
                self.profile_list.setCurrentRow(idx)
                QMessageBox.warning(self, "Validation", "Provider должен быть openai или gemini.")
                return None
            if not model_name:
                self.profile_list.setCurrentRow(idx)
                QMessageBox.warning(self, "Validation", "Model не может быть пустым.")
                return None

            requested_id = sanitize_profile_id(profile.get("id") or "")
            if not requested_id:
                requested_id = model_name
            profile_id = generate_profile_id(requested_id, used_ids)
            profiles.append(
                {
                    "id": profile_id,
                    "provider": provider,
                    "model": model_name,
                    "api_key": str(profile.get("api_key") or "").strip(),
                    "base_url": str(profile.get("base_url") or "").strip(),
                }
            )

        active = str(self._active_profile or "").strip()
        known_ids = {item["id"] for item in profiles}
        if active not in known_ids:
            active = profiles[0]["id"] if profiles else ""
        return {"active_profile": active or None, "profiles": profiles}

    def _save_and_accept(self) -> None:
        validated = self._validated_payload()
        if validated is None:
            return
        self._result_payload = normalize_profiles_payload(validated)
        self.accept()


class InfoPopupDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("InfoPopup")
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.resize(470, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)

        title = QLabel("Session information")
        title.setObjectName("SectionTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)

        hint = QLabel("Esc or click outside to close")
        hint.setObjectName("MetaText")
        title_row.addWidget(hint, 0, Qt.AlignRight)
        layout.addLayout(title_row)

        self.tabs = QTabWidget()
        self.overview_panel = OverviewPanelWidget()
        self.tools_panel = ToolsPanelWidget()

        help_widget = QWidget()
        help_layout = QVBoxLayout(help_widget)
        help_layout.setContentsMargins(0, 0, 0, 0)
        help_layout.setSpacing(0)
        self.help_text = QTextBrowser()
        self.help_text.setOpenLinks(False)
        self.help_text.setOpenExternalLinks(False)
        self.help_text.setReadOnly(True)
        help_layout.addWidget(self.help_text)

        self.tabs.addTab(self.overview_panel, _fa_icon("fa5s.info-circle", color=ACCENT_BLUE, size=14), "Info")
        self.tabs.addTab(self.tools_panel, _fa_icon("fa5s.tools", color=ACCENT_BLUE, size=14), "Tools")
        self.tabs.addTab(help_widget, _fa_icon("fa5s.question-circle", color=ACCENT_BLUE, size=14), "Help")
        layout.addWidget(self.tabs, 1)


class NoticeWidget(QFrame):
    def __init__(self, message: str, level: str = "info") -> None:
        super().__init__()
        self.setObjectName("FlatNoticeRow")
        self.setFrameShape(QFrame.NoFrame)
        self._level = "info"
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 1, 0, 1)
        layout.setSpacing(5)

        self.icon_label = QLabel()
        layout.addWidget(self.icon_label, 0, Qt.AlignTop)

        self.text_label = QLabel(message)
        self.text_label.setObjectName("MetaText")
        self.text_label.setWordWrap(True)
        layout.addWidget(self.text_label, 1)

        self.set_level(level)

    def _icon_for_level(self, level: str) -> tuple[str, str]:
        if level == "warning":
            return "fa5s.exclamation-triangle", AMBER_WARNING
        if level == "error":
            return "fa5s.times-circle", ERROR_RED
        if level == "success":
            return "fa5s.check-circle", SUCCESS_GREEN
        return "fa5s.info-circle", ACCENT_BLUE

    def set_level(self, level: str) -> None:
        normalized = str(level or "info").strip().lower() or "info"
        self._level = normalized
        icon_name, color = self._icon_for_level(normalized)
        self.icon_label.setPixmap(_fa_icon(icon_name, color=color, size=11).pixmap(11, 11))

    def set_message(self, message: str) -> None:
        self.text_label.setText(str(message or ""))


class RunStatsWidget(QWidget):
    def __init__(self, stats: str) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 60)
        layout.setSpacing(0)
        layout.addStretch(1)

        chip = QFrame()
        chip.setObjectName("TranscriptMetaChip")
        chip_layout = QHBoxLayout(chip)
        chip_layout.setContentsMargins(8, 4, 8, 4)
        chip_layout.setSpacing(5)

        icon = QLabel()
        icon.setPixmap(_fa_icon("fa5s.check-circle", color=SUCCESS_GREEN, size=11).pixmap(11, 11))
        chip_layout.addWidget(icon, 0, Qt.AlignVCenter)

        label = QLabel(stats)
        label.setObjectName("MetaText")
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        chip_layout.addWidget(label, 0, Qt.AlignVCenter)

        layout.addWidget(chip, 0, Qt.AlignRight)


class StatusIndicatorWidget(QFrame):
    def __init__(self, label: str) -> None:
        super().__init__()
        self.setObjectName("InlineStatusRow")
        self.setFrameShape(QFrame.NoFrame)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 6)
        layout.setSpacing(6)

        self.spinner = QToolButton()
        self.spinner.setObjectName("InlineStatusSpinner")
        self.spinner.setEnabled(False)
        self.spinner.setAutoRaise(True)
        self.spinner.setIcon(_fa_icon("fa5s.spinner", color=TEXT_MUTED, size=12))
        self.spinner.setIconSize(QSize(12, 12))
        self.spinner.setFixedSize(14, 14)
        layout.addWidget(self.spinner, 0, Qt.AlignVCenter)

        self.label = QLabel(label)
        self.label.setObjectName("TranscriptMeta")
        layout.addWidget(self.label, 0, Qt.AlignVCenter)
        layout.addStretch(1)

    def set_label(self, label: str) -> None:
        self.label.setText(label)


class UserMessageWidget(QFrame):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.full_text = text
        self.preview_text, self.is_expandable = _collapsed_user_message_text(text)
        self.setObjectName("TranscriptRow")
        self.setFrameShape(QFrame.NoFrame)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 16)
        layout.setSpacing(0)

        # Пружина выталкивает пузырь вправо
        layout.addStretch(1)

        self.bubble = QFrame()
        self.bubble.setObjectName("UserBubble")
        self.bubble.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        self.bubble.setMaximumWidth(720)

        bubble_layout = QVBoxLayout(self.bubble)
        bubble_layout.setContentsMargins(14, 10, 14, 10)
        bubble_layout.setSpacing(4)

        self.body = QLabel(self.preview_text if self.is_expandable else self.full_text)
        self.body.setObjectName("TranscriptBody")
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)

        from PySide6.QtGui import QTextDocument
        doc = QTextDocument()
        font = self.body.font()
        font.setPointSize(11)
        doc.setDefaultFont(font)
        doc.setPlainText(text)
        doc.setTextWidth(680)
        ideal_width = min(680, int(doc.idealWidth()) + 8)

        self.body.setMinimumWidth(ideal_width)
        self.body.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.body.setMaximumWidth(680)
        bubble_layout.addWidget(self.body)

        self.toggle_button = QToolButton()
        self.toggle_button.setObjectName("DisclosureButton")
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle_button.setAutoRaise(True)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setVisible(self.is_expandable)
        self.toggle_button.toggled.connect(self._set_expanded)
        bubble_layout.addWidget(self.toggle_button, 0, Qt.AlignRight)
        self._set_expanded(False)

        layout.addWidget(self.bubble)

    def _set_expanded(self, expanded: bool) -> None:
        self.body.setText(self.full_text if expanded else self.preview_text)
        self.toggle_button.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.toggle_button.setText("Show less" if expanded else "Show more")


class AssistantMessageWidget(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("TranscriptRow")
        self.setFrameShape(QFrame.NoFrame)
        self._markdown = ""
        
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 1, 0, 3)
        self._layout.setSpacing(6)

        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(8)
        self._layout.addWidget(self.content_widget, 1)

        self.parts_widgets = []

    def set_markdown(self, markdown: str) -> None:
        self._markdown = markdown
        text = markdown.strip() or "*Thinking…*"
        
        parts = text.split("```")

        while len(self.parts_widgets) < len(parts):
            idx = len(self.parts_widgets)
            is_code = (idx % 2 == 1)
            if is_code:
                w = CodeBlockWidget("", "")
                self.content_layout.addWidget(w)
            else:
                w = AutoTextBrowser()
                w.setObjectName("AssistantBody")
                self.content_layout.addWidget(w)
            self.parts_widgets.append(w)

        while len(self.parts_widgets) > len(parts):
            w = self.parts_widgets.pop()
            self.content_layout.removeWidget(w)
            w.deleteLater()

        for idx, part in enumerate(parts):
            w = self.parts_widgets[idx]
            is_code = (idx % 2 == 1)
            
            if is_code:
                lines = part.split("\n", 1)
                lang = lines[0].strip() if len(lines) > 0 else ""
                code = lines[1] if len(lines) > 1 else ""
                title = lang.upper() if lang else "CODE"
                w.set_code(code, lang, title)
                w.setVisible(True)
            else:
                if part.strip() or idx == 0:
                    w.setMarkdown(part)
                    w.setVisible(True)
                else:
                    w.setVisible(False)

    def markdown(self) -> str:
        return self._markdown


class CliExecWidget(QFrame):
    def __init__(self, command: str) -> None:
        super().__init__()
        self.setObjectName("CliExecPanel")
        self.setFrameShape(QFrame.NoFrame)
        self._programmatic_scroll = False
        self._auto_follow_enabled = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(10, 8, 10, 6)
        header_row.setSpacing(6)

        self.command_label = ElidedLabel(elide_mode=Qt.ElideRight)
        self.command_label.setObjectName("CliExecHeader")
        self.command_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.command_label.setWordWrap(False)
        self.command_label.setMinimumWidth(0)
        header_row.addWidget(self.command_label, 1)

        self.meta_label = QLabel("")
        self.meta_label.setObjectName("MetaText")
        self.meta_label.setVisible(False)
        header_row.addWidget(self.meta_label, 0, Qt.AlignRight | Qt.AlignVCenter)
        layout.addLayout(header_row)

        self.output_view = QPlainTextEdit()
        self.output_view.setObjectName("CliExecOutput")
        self.output_view.setReadOnly(True)
        self.output_view.setFont(_make_mono_font())
        self.output_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.output_view.setMinimumHeight(44)
        self.output_view.setMaximumHeight(156)
        self.output_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.output_view.document().setMaximumBlockCount(1200)
        self.output_view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.output_view.verticalScrollBar().valueChanged.connect(self._on_scroll_value_changed)
        layout.addWidget(self.output_view)

        self.set_command(command)
        self._sync_output_height()

    def set_command(self, command: str) -> None:
        raw = str(command or "")
        stripped = raw.strip()
        compact = " ".join(stripped.split())
        rendered = f"$ {compact}" if compact else "$ "
        self.command_label.set_full_text(rendered)

    def set_meta(self, text: str) -> None:
        value = str(text or "").strip()
        self.meta_label.setText(value)
        self.meta_label.setVisible(bool(value))

    def _is_near_bottom(self, threshold: int = 16) -> bool:
        scrollbar = self.output_view.verticalScrollBar()
        return (scrollbar.maximum() - scrollbar.value()) <= max(threshold, scrollbar.pageStep() // 8)

    def _on_scroll_value_changed(self, _value: int) -> None:
        if self._programmatic_scroll:
            return
        self._auto_follow_enabled = self._is_near_bottom()

    def _scroll_to_bottom(self) -> None:
        scrollbar = self.output_view.verticalScrollBar()
        self._programmatic_scroll = True
        scrollbar.setValue(scrollbar.maximum())
        self._programmatic_scroll = False
        self._auto_follow_enabled = True

    def _sync_output_height(self) -> None:
        _sync_plain_text_height(self.output_view, min_lines=2, max_lines=7, extra_padding=14)

    def append_output(self, text: str, stream: str = "stdout") -> None:
        chunk = str(text or "")
        if not chunk:
            return
        _ = stream
        scrollbar = self.output_view.verticalScrollBar()
        follow = self._auto_follow_enabled or self._is_near_bottom()
        previous_value = scrollbar.value()

        cursor = self.output_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(chunk)
        self.output_view.setTextCursor(cursor)
        self._sync_output_height()

        if follow:
            self._scroll_to_bottom()
            return

        self._programmatic_scroll = True
        scrollbar.setValue(previous_value)
        self._programmatic_scroll = False

    def has_output(self) -> bool:
        return bool(self.output_view.toPlainText())

    def ensure_final_output(self, final_text: str) -> None:
        target = str(final_text or "")
        if not target:
            return
        current = self.output_view.toPlainText()
        if not current:
            self.output_view.setPlainText(target)
            self._sync_output_height()
            self._scroll_to_bottom()
            return
        if current == target:
            return
        if target.startswith(current):
            self.append_output(target[len(current) :], stream="stdout")
            return
        self.output_view.setPlainText(target)
        self._sync_output_height()
        self._scroll_to_bottom()


class ToolCardWidget(QFrame):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__()
        self.setObjectName("ToolRow")
        self.setFrameShape(QFrame.NoFrame)
        self.tool_id = payload.get("tool_id", "")
        self.payload = payload
        self.output_section: CollapsibleSection | None = None
        self.output_view: QPlainTextEdit | None = None
        self.cli_exec_widget: CliExecWidget | None = None
        self._args_expanded = False
        self._is_cli_exec = self._is_cli_exec_name(payload.get("name", ""))
        self._cli_expanded = True
        self._cli_user_toggled = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 2)
        layout.setSpacing(2)

        self.header_container = QWidget()
        header = QHBoxLayout(self.header_container)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        self.icon_label = QLabel()
        self.icon_label.setPixmap(_fa_icon("fa5s.circle", color=TEXT_MUTED, size=7).pixmap(7, 7))
        header.addWidget(self.icon_label, 0, Qt.AlignVCenter)

        self.tool_button = QPushButton()
        self.tool_button.setObjectName("ToolCallButton")
        self.tool_button.setCheckable(True)
        self.tool_button.setFlat(True)
        self.tool_button.setText(payload.get("display", "") or payload.get("name", "tool"))
        self.tool_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.tool_button.setMinimumWidth(0)
        self.tool_button.setCursor(Qt.PointingHandCursor)
        self.tool_button.setIcon(_fa_icon("fa5s.caret-right", color=TEXT_MUTED, size=8))
        self.tool_button.setIconSize(QSize(8, 8))
        if not self._is_cli_exec:
            self.tool_button.toggled.connect(self._set_args_expanded)
        else:
            self.tool_button.toggled.connect(self._set_cli_expanded)
            self.tool_button.clicked.connect(self._mark_cli_toggled_by_user)
        header.addWidget(self.tool_button, 1)

        self.timing_label = QLabel("")
        self.timing_label.setObjectName("MetaText")
        self.timing_label.setVisible(False)
        header.addWidget(self.timing_label, 0, Qt.AlignVCenter | Qt.AlignRight)
        layout.addWidget(self.header_container)

        self.args_view = QPlainTextEdit()
        self.args_view.setObjectName("InlineCodeView")
        self.args_view.setReadOnly(True)
        self.args_view.setFont(_make_mono_font())
        self.args_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._set_args(payload.get("args", {}))

        self.args_container = QWidget()
        args_layout = QHBoxLayout(self.args_container)
        args_layout.setContentsMargins(16, 0, 0, 0)
        args_layout.setSpacing(0)
        args_layout.addWidget(self.args_view, 1)
        self.args_container.setVisible(False)
        layout.addWidget(self.args_container)

        self.diff_section: CollapsibleSection | None = None

        if self._is_cli_exec:
            self.args_container.setVisible(False)
            self._ensure_cli_exec_widget()
            self.tool_button.setChecked(True)
            self._set_cli_expanded(True)

    @staticmethod
    def _normalize_args(args: Any) -> dict[str, Any]:
        return dict(args) if isinstance(args, dict) else {}

    @staticmethod
    def _is_cli_exec_name(name: Any) -> bool:
        return str(name or "").strip().lower() == "cli_exec"

    @staticmethod
    def _command_from_args(args: dict[str, Any]) -> str:
        return str(args.get("command", "") or "")

    @staticmethod
    def _command_from_display(display: Any, tool_name: Any) -> str:
        text = str(display or "").strip()
        name = str(tool_name or "").strip() or "cli_exec"
        prefix = f"{name}("
        if text.startswith(prefix) and text.endswith(")"):
            inner = text[len(prefix):-1].strip()
            if len(inner) >= 2 and inner[:1] == inner[-1:] and inner[:1] in {'"', "'"}:
                inner = inner[1:-1]
            return inner
        return ""

    def _ensure_cli_exec_widget(self) -> CliExecWidget:
        if self.cli_exec_widget is None:
            command = self._command_from_args(self._normalize_args(self.payload.get("args", {})))
            self.cli_exec_widget = CliExecWidget(command)
            self.layout().addWidget(self.cli_exec_widget)
        return self.cli_exec_widget

    def _set_args(self, args: Any) -> None:
        normalized = self._normalize_args(args)
        rendered = json.dumps(normalized, ensure_ascii=False, indent=2)
        if self.args_view.toPlainText() != rendered:
            self.args_view.setPlainText(rendered)
        _sync_plain_text_height(self.args_view, min_lines=2, max_lines=8, extra_padding=14)

    def append_cli_output(self, text: str, stream: str = "stdout") -> None:
        if not text:
            return
        if not self._is_cli_exec:
            return
        self._ensure_cli_exec_widget().append_output(text, stream=stream)

    def update_started_payload(self, payload: dict[str, Any]) -> None:
        normalized_args = self._normalize_args(payload.get("args", self.payload.get("args", {})))
        merged_payload = dict(self.payload)
        merged_payload.update(payload)
        merged_payload["args"] = normalized_args
        self.payload = merged_payload

        display = str(self.payload.get("display", "") or "").strip()
        if display:
            self.tool_button.setText(display)
        self._set_args(normalized_args)

        self._is_cli_exec = self._is_cli_exec or self._is_cli_exec_name(self.payload.get("name", ""))
        if self._is_cli_exec:
            cli_exec_widget = self._ensure_cli_exec_widget()
            command = self._command_from_args(normalized_args)
            if not command:
                command = self._command_from_display(display, self.payload.get("name", "cli_exec"))
            if command:
                cli_exec_widget.set_command(command)
            if not self.tool_button.isChecked():
                self.tool_button.setChecked(True)
            self._set_cli_expanded(self.tool_button.isChecked())

    def finish(self, payload: dict[str, Any]) -> None:
        previous_display = self.tool_button.text()
        normalized_args = self._normalize_args(payload.get("args", self.payload.get("args", {})))
        merged_payload = dict(self.payload)
        merged_payload.update(payload)
        merged_payload["args"] = normalized_args
        self.payload = merged_payload
        is_error = payload.get("is_error", False)
        icon_name = "fa5s.circle" if not is_error else "fa5s.times-circle"
        color = SUCCESS_GREEN if not is_error else ERROR_RED
        self.icon_label.setPixmap(_fa_icon(icon_name, color=color, size=7).pixmap(7, 7))
        self.tool_button.setText(self.payload.get("display", "") or previous_display or self.payload.get("name", "tool"))
        self._set_args(normalized_args)

        duration = self.payload.get("duration")
        if duration is not None:
            self.timing_label.setText(f"{duration:.1f}s")
            self.timing_label.setVisible(True)

        self._is_cli_exec = self._is_cli_exec or self._is_cli_exec_name(self.payload.get("name", ""))
        if self._is_cli_exec:
            cli_exec_widget = self._ensure_cli_exec_widget()
            cli_exec_widget.set_command(self._command_from_args(normalized_args))
            status_text = ""
            if duration is not None:
                status_text = f"{duration:.1f}s"
            if is_error:
                status_text = f"error · {status_text}" if status_text else "error"
            cli_exec_widget.set_meta(status_text)
            cli_exec_widget.ensure_final_output(str(self.payload.get("content", "") or ""))
            # Default behavior: collapse finished cli cards to keep transcript compact.
            # If user explicitly toggled this card, preserve their chosen state.
            if not self._cli_user_toggled:
                self.tool_button.setChecked(False)
            self._set_cli_expanded(self.tool_button.isChecked())
            return

        if is_error:
            if self.output_view is None:
                self.output_view = QPlainTextEdit()
                self.output_view.setObjectName("InlineCodeView")
                self.output_view.setReadOnly(True)
                self.output_view.setFont(_make_mono_font())
            if self.output_section is None:
                self.output_section = CollapsibleSection("Output", self.output_view, expanded=False)
                self.layout().insertWidget(1, self.output_section)
            self.output_section.setVisible(True)
            self.output_view.setPlainText(self.payload.get("content", ""))
            _sync_plain_text_height(self.output_view, min_lines=2, max_lines=10, extra_padding=14)
        elif self.output_section is not None:
            self.output_section.setVisible(False)

        diff_text = self.payload.get("diff", "")
        if diff_text and self.diff_section is None:
            self.diff_section = CollapsibleSection(
                "Diff",
                DiffBlockWidget(diff_text, source_path=str(normalized_args.get("path", "") or "")),
                expanded=False,
            )
            self.layout().addWidget(self.diff_section)
        elif diff_text and self.diff_section is not None:
            if isinstance(self.diff_section.content, DiffBlockWidget):
                self.diff_section.content.set_diff(diff_text, source_path=str(normalized_args.get("path", "") or ""))
            elif isinstance(self.diff_section.content, CodeBlockWidget):
                self.diff_section.content.set_code(diff_text, "diff")

    def _set_args_expanded(self, expanded: bool) -> None:
        if self._is_cli_exec:
            return
        self._args_expanded = expanded
        self.tool_button.setIcon(
            _fa_icon("fa5s.caret-down" if expanded else "fa5s.caret-right", color=TEXT_MUTED, size=8)
        )
        self.args_container.setVisible(expanded)

    def _set_cli_expanded(self, expanded: bool) -> None:
        if not self._is_cli_exec:
            return
        self._cli_expanded = expanded
        self.tool_button.setIcon(
            _fa_icon("fa5s.caret-down" if expanded else "fa5s.caret-right", color=TEXT_MUTED, size=8)
        )
        if self.cli_exec_widget is not None:
            self.cli_exec_widget.setVisible(expanded)

    def _mark_cli_toggled_by_user(self) -> None:
        if not self._is_cli_exec:
            return
        self._cli_user_toggled = True


class ConversationTurnWidget(QWidget):
    def __init__(self, user_text: str) -> None:
        super().__init__()
        self._assistant_markdown = ""
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._timeline: list[tuple[str, QWidget]] = []
        self.assistant_segments: list[AssistantMessageWidget] = []
        self.tool_cards: dict[str, ToolCardWidget] = {}
        self.status_widget: StatusIndicatorWidget | None = None
        self.summary_notice_widget: NoticeWidget | None = None
        self._append_block("user", UserMessageWidget(user_text))

    @staticmethod
    def _common_prefix_length(first: str, second: str) -> int:
        limit = min(len(first), len(second))
        index = 0
        while index < limit and first[index] == second[index]:
            index += 1
        return index

    def _append_block(self, kind: str, widget: QWidget) -> QWidget:
        if kind in {"assistant", "tool", "notice", "stats"}:
            self.clear_summary_notice()
            self.clear_status()
        self._layout.addWidget(widget)
        self._timeline.append((kind, widget))
        return widget

    def has_rendered_output(self) -> bool:
        return len(self._timeline) > 1

    def set_status(self, label: str) -> None:
        if self.status_widget is None:
            self.status_widget = StatusIndicatorWidget(label)
            self._layout.addWidget(self.status_widget)
            return
        self._layout.removeWidget(self.status_widget)
        self._layout.addWidget(self.status_widget)
        self.status_widget.set_label(label)

    def clear_status(self) -> None:
        if self.status_widget is None:
            return
        self._layout.removeWidget(self.status_widget)
        self.status_widget.deleteLater()
        self.status_widget = None

    def set_summary_notice(self, message: str, level: str = "info") -> None:
        text = str(message or "").strip()
        if not text:
            return
        if self.summary_notice_widget is None:
            self.summary_notice_widget = NoticeWidget(text, level=level)
            self._layout.addWidget(self.summary_notice_widget)
        else:
            self.summary_notice_widget.set_message(text)
            self.summary_notice_widget.set_level(level)
        if self.status_widget is not None:
            self._layout.removeWidget(self.status_widget)
            self._layout.addWidget(self.status_widget)

    def clear_summary_notice(self) -> None:
        if self.summary_notice_widget is None:
            return
        self._layout.removeWidget(self.summary_notice_widget)
        self.summary_notice_widget.deleteLater()
        self.summary_notice_widget = None

    def _ensure_assistant_segment(self) -> AssistantMessageWidget:
        if self._timeline and self._timeline[-1][0] == "assistant":
            return self._timeline[-1][1]  # type: ignore[return-value]
        segment = AssistantMessageWidget()
        self.assistant_segments.append(segment)
        self._append_block("assistant", segment)
        return segment

    def set_assistant_markdown(self, markdown: str) -> None:
        if markdown == self._assistant_markdown and self.assistant_segments:
            return

        if not self._assistant_markdown:
            segment_text = markdown
        elif markdown.startswith(self._assistant_markdown):
            segment_text = markdown[len(self._assistant_markdown):]
        else:
            prefix_len = self._common_prefix_length(self._assistant_markdown, markdown)
            segment_text = markdown[prefix_len:]

        segment = self._ensure_assistant_segment()
        if markdown and not segment_text and not segment.markdown():
            segment.set_markdown(markdown)
        elif segment_text:
            segment.set_markdown(segment.markdown() + segment_text)
        elif not segment.markdown():
            segment.set_markdown(markdown)

        self._assistant_markdown = markdown

    def add_notice(self, message: str, level: str = "info") -> None:
        self._append_block("notice", NoticeWidget(message, level=level))

    def add_assistant_message(self, markdown: str) -> AssistantMessageWidget:
        segment = AssistantMessageWidget()
        segment.set_markdown(markdown)
        self.assistant_segments.append(segment)
        self._append_block("assistant", segment)
        self._assistant_markdown = markdown
        return segment

    def start_tool(self, payload: dict[str, Any]) -> ToolCardWidget:
        tool_id = payload.get("tool_id", "")
        card = self.tool_cards.get(tool_id)
        if card is None:
            card = ToolCardWidget(payload)
            self.tool_cards[tool_id] = card
            self._append_block("tool", card)
        else:
            card.update_started_payload(payload)
        return card

    def finish_tool(self, payload: dict[str, Any]) -> None:
        self.start_tool(payload).finish(payload)

    def append_tool_output(self, payload: dict[str, Any]) -> None:
        tool_id = str(payload.get("tool_id", "") or "").strip()
        if not tool_id:
            return
        card = self.tool_cards.get(tool_id)
        if card is None:
            card = self.start_tool(
                {
                    "tool_id": tool_id,
                    "name": "cli_exec",
                    "args": {},
                    "display": "cli_exec",
                }
            )
        card.append_cli_output(
            str(payload.get("data", "") or ""),
            stream=str(payload.get("stream", "stdout") or "stdout"),
        )

    def complete(self, stats: str) -> None:
        self._append_block("stats", RunStatsWidget(stats))

    def restore_blocks(self, blocks: list[dict[str, Any]]) -> None:
        for block in blocks:
            block_type = block.get("type")
            if block_type == "assistant":
                markdown = str(block.get("markdown", "") or "").strip()
                if markdown:
                    self.add_assistant_message(markdown)
            elif block_type == "tool":
                payload = dict(block.get("payload") or {})
                if payload:
                    self.finish_tool(payload)
            elif block_type == "notice":
                message = str(block.get("message", "") or "").strip()
                if message:
                    self.add_notice(message, str(block.get("level") or "info"))
            elif block_type == "stats":
                stats = str(block.get("stats", "") or "").strip()
                if stats:
                    self.complete(stats)

    def block_kinds(self) -> list[str]:
        return [kind for kind, _widget in self._timeline]


class ChatTranscriptWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._auto_follow_enabled = True
        self._pending_scroll = False
        self._pending_force_scroll = False
        self._programmatic_scroll = False
        self._range_follow_ticket = 0
        self._range_follow_force = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.container = QWidget()
        self.container.setObjectName("TranscriptContainer")
        shell = QHBoxLayout(self.container)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        shell.addStretch(1)

        self.column = QWidget()
        self.column.setObjectName("TranscriptColumn")
        self.column.setMaximumWidth(TRANSCRIPT_MAX_WIDTH)
        self.column.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.layout = QVBoxLayout(self.column)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(4)
        self.layout.addStretch(1)

        shell.addWidget(self.column, 3)
        shell.addStretch(1)
        self.scroll.setWidget(self.container)
        outer.addWidget(self.scroll)
        scrollbar = self.scroll.verticalScrollBar()
        scrollbar.valueChanged.connect(self._handle_scrollbar_value_changed)
        scrollbar.rangeChanged.connect(self._handle_scrollbar_range_changed)

    def clear_transcript(self) -> None:
        while self.layout.count() > 1:
            item = self.layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._auto_follow_enabled = True
        self._pending_scroll = False
        self._pending_force_scroll = False
        self._range_follow_ticket = 0
        self._range_follow_force = False

    def add_global_notice(self, message: str, level: str = "info") -> None:
        self.layout.insertWidget(self.layout.count() - 1, NoticeWidget(message, level=level))
        self.notify_content_changed()

    def start_turn(self, user_text: str) -> ConversationTurnWidget:
        turn = ConversationTurnWidget(user_text)
        self.layout.insertWidget(self.layout.count() - 1, turn)
        self.notify_content_changed(force=True)
        return turn

    def load_transcript(self, payload: dict[str, Any] | None) -> None:
        self.clear_transcript()
        payload = payload or {}
        summary_notice = str(payload.get("summary_notice", "") or "").strip()
        if summary_notice:
            self.add_global_notice(summary_notice, level="info")
        for turn_data in payload.get("turns", []) or []:
            user_text = str(turn_data.get("user_text", "") or "")
            turn = ConversationTurnWidget(user_text)
            turn.restore_blocks(list(turn_data.get("blocks", []) or []))
            self.layout.insertWidget(self.layout.count() - 1, turn)
        self.notify_content_changed(force=True)

    @property
    def auto_follow_enabled(self) -> bool:
        return self._auto_follow_enabled

    def is_near_bottom(self, threshold: int = 28) -> bool:
        scrollbar = self.scroll.verticalScrollBar()
        return (scrollbar.maximum() - scrollbar.value()) <= max(threshold, scrollbar.pageStep() // 8)

    def _handle_scrollbar_value_changed(self, _value: int) -> None:
        if self._programmatic_scroll:
            return
        self._auto_follow_enabled = self.is_near_bottom()
        if not self._auto_follow_enabled:
            self._range_follow_force = False

    def _handle_scrollbar_range_changed(self, _minimum: int, _maximum: int) -> None:
        if not self._range_follow_ticket:
            return
        self._follow_to_bottom(self._range_follow_ticket)

    def notify_content_changed(self, *, force: bool = False) -> None:
        self.queue_scroll_to_bottom(force=force)

    def queue_scroll_to_bottom(self, *, force: bool = False) -> None:
        if force:
            self._pending_force_scroll = True
        if self._pending_scroll:
            return
        self._pending_scroll = True
        QTimer.singleShot(0, self._flush_pending_scroll)

    def _flush_pending_scroll(self) -> None:
        self._pending_scroll = False
        force = self._pending_force_scroll
        self._pending_force_scroll = False
        if not force and not self._auto_follow_enabled:
            self._range_follow_ticket += 1
            self._range_follow_force = False
            return

        self._range_follow_force = force
        self._scrollbar_to_bottom()
        self._auto_follow_enabled = True
        self._schedule_follow_up_scroll(force=force)

    def _scrollbar_to_bottom(self) -> None:
        scrollbar = self.scroll.verticalScrollBar()
        self._programmatic_scroll = True
        scrollbar.setValue(scrollbar.maximum())
        self._programmatic_scroll = False

    def _schedule_follow_up_scroll(self, *, force: bool) -> None:
        self._range_follow_force = force
        self._range_follow_ticket += 1
        ticket = self._range_follow_ticket
        follow_delays = (0, 20, 80)
        for delay in follow_delays:
            QTimer.singleShot(delay, lambda current=ticket: self._follow_to_bottom(current))
        QTimer.singleShot(max(follow_delays) + 12, lambda current=ticket: self._finish_follow_up(current))

    def _follow_to_bottom(self, ticket: int) -> None:
        if ticket != self._range_follow_ticket:
            return
        if not self._range_follow_force and not self._auto_follow_enabled:
            return
        self._scrollbar_to_bottom()
        self._auto_follow_enabled = True

    def _finish_follow_up(self, ticket: int) -> None:
        if ticket != self._range_follow_ticket:
            return
        self._range_follow_force = False

    def scroll_to_bottom(self) -> None:
        self._pending_scroll = False
        self._pending_force_scroll = False
        self._scrollbar_to_bottom()
        self._auto_follow_enabled = True
        self._schedule_follow_up_scroll(force=True)


class ApprovalDialog(QDialog):
    def __init__(self, payload: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.choice: tuple[bool, bool] = (False, False)
        self.setWindowTitle("Confirmation Needed")
        self.setModal(True)
        self.resize(470, 320)
        self.setMinimumSize(420, 280)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel("Protected action requires confirmation")
        title.setStyleSheet("font-weight: 600; font-size: 13pt;")
        layout.addWidget(title)

        summary = payload.get("summary", {})
        impacts = ", ".join(summary.get("impacts", [])) or "local state"
        summary_label = QLabel(
            f"Risk: {summary.get('risk_level', 'unknown')} • Impacts: {impacts} • "
            f"Default: {'approve' if summary.get('default_approve') else 'deny'}"
        )
        summary_label.setStyleSheet(f"color: {TEXT_MUTED};")
        summary_label.setWordWrap(True)
        layout.addWidget(summary_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(10)
        for tool in payload.get("tools", []):
            card = QFrame()
            card.setObjectName("ApprovalCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 10, 10, 10)
            card_layout.setSpacing(6)
            name_label = QLabel(tool.get("name", "tool"))
            name_label.setStyleSheet("font-weight: 600;")
            card_layout.addWidget(name_label)
            args_view = QPlainTextEdit()
            args_view.setObjectName("CodeView")
            args_view.setReadOnly(True)
            args_view.setFont(_make_mono_font())
            args_view.setPlainText(json.dumps(tool.get("args", {}), ensure_ascii=False, indent=2))
            args_view.setFixedHeight(78)
            card_layout.addWidget(CollapsibleSection("Arguments", args_view, expanded=False))
            container_layout.addWidget(card)
        container_layout.addStretch(1)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox()
        approve_button = QPushButton(_fa_icon("fa5s.check", color="white", size=12), "Approve")
        approve_button.setObjectName("PrimaryButton")
        deny_button = QPushButton(_fa_icon("fa5s.times", color="white", size=12), "Deny")
        deny_button.setObjectName("DangerButton")
        always_button = QPushButton("Always for this session")
        buttons.addButton(approve_button, QDialogButtonBox.AcceptRole)
        buttons.addButton(always_button, QDialogButtonBox.ActionRole)
        buttons.addButton(deny_button, QDialogButtonBox.RejectRole)
        layout.addWidget(buttons)

        approve_button.clicked.connect(self._approve)
        always_button.clicked.connect(self._always)
        deny_button.clicked.connect(self._deny)

    def _approve(self) -> None:
        self.choice = (True, False)
        self.accept()

    def _always(self) -> None:
        self.choice = (True, True)
        self.accept()

    def _deny(self) -> None:
        self.choice = (False, False)
        self.reject()
