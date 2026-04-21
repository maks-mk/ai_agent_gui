from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import qtawesome as qta
from PySide6.QtCore import QAbstractListModel, QModelIndex, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMenu,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ui.theme import SURFACE_ALT, TEXT_MUTED, TEXT_PRIMARY


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
        return "now"
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "now"
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"
    days = hours // 24
    if days < 7:
        return f"{days}d"
    weeks = days // 7
    if weeks < 5:
        return f"{weeks}w"
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


def _session_preview(session: dict[str, str]) -> str:
    project_path = str(session.get("project_path", "") or "").replace("\\", "/").strip()
    title = str(session.get("title", "") or "").strip()
    if project_path:
        project_name = _sidebar_project_name(project_path)
        if title and title != project_name:
            return project_path
        return f"Folder: {project_path}"
    return "Open this chat"


class SessionListModel(QAbstractListModel):
    KindRole = Qt.UserRole + 1
    SessionIdRole = Qt.UserRole + 2
    TitleRole = Qt.UserRole + 3
    UpdatedAtRole = Qt.UserRole + 4
    ProjectPathRole = Qt.UserRole + 5
    ProjectTitleRole = Qt.UserRole + 6
    PreviewRole = Qt.UserRole + 7

    def __init__(self) -> None:
        super().__init__()
        self._items: list[dict[str, str]] = []
        self._source_sessions: list[dict[str, str]] = []
        self._filter_text = ""

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
        if role == self.PreviewRole:
            return item.get("preview", "")
        return None

    def set_sessions(self, sessions: list[dict[str, str]]) -> None:
        self._source_sessions = [dict(row) for row in sessions]
        self._rebuild_items()

    def set_filter_text(self, value: str) -> None:
        text = str(value or "").strip().lower()
        if text == self._filter_text:
            return
        self._filter_text = text
        self._rebuild_items()

    def _rebuild_items(self) -> None:
        filter_text = self._filter_text
        filtered_sessions: list[dict[str, str]] = []
        for raw in self._source_sessions:
            row = dict(raw)
            row["preview"] = _session_preview(row)
            if filter_text:
                haystack = " ".join(
                    [
                        str(row.get("title", "") or ""),
                        str(row.get("project_path", "") or ""),
                        str(row.get("preview", "") or ""),
                    ]
                ).lower()
                if filter_text not in haystack:
                    continue
            filtered_sessions.append(row)

        grouped: dict[str, list[dict[str, str]]] = {}
        for row in filtered_sessions:
            project_key = str(row.get("project_path", "")).strip()
            grouped.setdefault(project_key, []).append(row)

        project_rows: list[tuple[str, list[dict[str, str]]]] = sorted(
            grouped.items(),
            key=lambda pair: max(
                (_sidebar_dt(item.get("updated_at", "")) for item in pair[1]),
                default=datetime.fromtimestamp(0, tz=timezone.utc),
            ),
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
                    "preview": "",
                }
            )
            sorted_rows = sorted(rows, key=lambda item: _sidebar_dt(item.get("updated_at", "")), reverse=True)
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

    def has_source_sessions(self) -> bool:
        return bool(self._source_sessions)


class SessionItemDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:  # type: ignore[override]
        painter.save()
        item_kind = str(index.data(SessionListModel.KindRole) or "session")
        rect = option.rect.adjusted(6, 3, -6, -3)
        is_selected = bool(option.state & QStyle.State_Selected)
        is_hovered = bool(option.state & QStyle.State_MouseOver)

        if item_kind == "group":
            icon_rect = rect.adjusted(2, 4, 0, 0)
            painter.drawPixmap(icon_rect.left(), icon_rect.top(), qta.icon("fa5.folder-open", color=TEXT_MUTED).pixmap(12, 12))
            group_text = str(index.data(SessionListModel.ProjectTitleRole) or index.data(SessionListModel.TitleRole) or "")
            title_rect = rect.adjusted(20, 0, -8, 0)
            title_font = option.font
            title_font.setPointSize(9)
            title_font.setWeight(QFont.DemiBold)
            painter.setFont(title_font)
            painter.setPen(QColor(TEXT_MUTED))
            painter.drawText(title_rect, Qt.AlignLeft | Qt.AlignVCenter, group_text.upper())
            painter.restore()
            return

        background = QColor(0, 0, 0, 0)
        if is_selected:
            background = QColor(SURFACE_ALT)
        elif is_hovered:
            background = QColor(SURFACE_ALT)
            background.setAlpha(110)
        if background.alpha() > 0:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(Qt.NoPen)
            painter.setBrush(background)
            painter.drawRoundedRect(rect, 12, 12)

        title = str(index.data(SessionListModel.TitleRole) or "")
        preview = str(index.data(SessionListModel.PreviewRole) or "")
        updated_at = _format_sidebar_time(str(index.data(SessionListModel.UpdatedAtRole) or ""))

        title_font = option.font
        title_font.setPointSize(10)
        title_font.setWeight(QFont.DemiBold if is_selected else QFont.Medium)
        title_metrics = QFontMetrics(title_font)

        meta_font = option.font
        meta_font.setPointSize(8)
        meta_font.setWeight(QFont.Medium)
        meta_metrics = QFontMetrics(meta_font)

        time_width = max(34, meta_metrics.horizontalAdvance(updated_at) + 6)
        content_rect = rect.adjusted(12, 7, -12, -7)
        title_rect = content_rect.adjusted(0, 0, -(time_width + 8), -18)
        time_rect = content_rect.adjusted(content_rect.width() - time_width, 0, 0, -18)
        preview_rect = content_rect.adjusted(0, 20, 0, 0)

        painter.setFont(title_font)
        painter.setPen(QColor(TEXT_PRIMARY))
        painter.drawText(
            title_rect,
            Qt.AlignLeft | Qt.AlignTop,
            title_metrics.elidedText(title, Qt.ElideRight, max(10, title_rect.width())),
        )

        painter.setFont(meta_font)
        painter.setPen(QColor(TEXT_MUTED))
        painter.drawText(time_rect, Qt.AlignRight | Qt.AlignTop, updated_at)
        painter.drawText(
            preview_rect,
            Qt.AlignLeft | Qt.AlignTop,
            meta_metrics.elidedText(preview, Qt.ElideRight, max(10, preview_rect.width())),
        )
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # type: ignore[override]
        _ = option
        kind = str(index.data(SessionListModel.KindRole) or "session")
        return QSize(260, 24 if kind == "group" else 56)


class SessionSidebarWidget(QWidget):
    session_activated = Signal(str)
    session_delete_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)

        title = QLabel("Chats")
        title.setObjectName("SidebarSectionTitle")
        header_row.addWidget(title, 1)

        self.delete_button = QToolButton()
        self.delete_button.setObjectName("SidebarGhostButton")
        self.delete_button.setIcon(qta.icon("fa5s.trash", color=TEXT_MUTED))
        self.delete_button.setToolTip("Delete selected chat")
        self.delete_button.setEnabled(False)
        self.delete_button.setAutoRaise(True)
        self.delete_button.clicked.connect(self._delete_selected_session)
        header_row.addWidget(self.delete_button, 0, Qt.AlignRight)
        root.addLayout(header_row)

        self.search_field = QLineEdit()
        self.search_field.setObjectName("SidebarSearchField")
        self.search_field.setPlaceholderText("Search chats")
        self.search_field.setClearButtonEnabled(True)
        self.search_field.setAccessibleName("Chat search")
        self.search_field.setAccessibleDescription("Filter chats by title or folder")
        root.addWidget(self.search_field)

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
        self.list_view.setAccessibleName("Chat list")
        self.list_view.setAccessibleDescription("Browse and open saved chats")

        self.model = SessionListModel()
        self.delegate = SessionItemDelegate()
        self.list_view.setModel(self.model)
        self.list_view.setItemDelegate(self.delegate)
        self.list_view.clicked.connect(self._emit_clicked_session)
        self.list_view.customContextMenuRequested.connect(self._show_context_menu)
        self.list_view.selectionModel().currentChanged.connect(self._on_current_changed)
        root.addWidget(self.list_view, 1)

        self.empty_label = QLabel("No chats yet")
        self.empty_label.setObjectName("SidebarEmptyState")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.empty_label.setVisible(False)
        root.addWidget(self.empty_label)

        self.search_field.textChanged.connect(self._on_search_changed)

    def set_sessions(self, sessions: list[dict[str, str]], active_session_id: str) -> None:
        self.model.set_sessions(sessions)
        self._refresh_empty_state()
        if not active_session_id:
            self.list_view.clearSelection()
            self._update_action_state()
            return
        index = self.model.index_for_session(active_session_id)
        if index.isValid():
            self.list_view.setCurrentIndex(index)
            self.list_view.scrollTo(index)
        else:
            self.list_view.clearSelection()
        self._update_action_state()

    def title_for_session(self, session_id: str) -> str:
        return self.model.title_for_session(session_id)

    def _on_search_changed(self, value: str) -> None:
        self.model.set_filter_text(value)
        self._refresh_empty_state()
        self._update_action_state()

    def _refresh_empty_state(self) -> None:
        if self.model.session_row_count() > 0:
            self.empty_label.setVisible(False)
            self.list_view.setVisible(True)
            return
        self.list_view.setVisible(False)
        if self.model.has_source_sessions():
            self.empty_label.setText("No chats match this search")
        else:
            self.empty_label.setText("No chats yet")
        self.empty_label.setVisible(True)

    def _emit_clicked_session(self, index: QModelIndex) -> None:
        session_id = self.model.session_id_at(index)
        if session_id:
            self.session_activated.emit(session_id)

    def _selected_session_id(self) -> str:
        return self.model.session_id_at(self.list_view.currentIndex())

    def _update_action_state(self) -> None:
        self.delete_button.setEnabled(bool(self._selected_session_id()))

    def _on_current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        _ = current
        self._update_action_state()

    def _delete_selected_session(self) -> None:
        session_id = self._selected_session_id()
        if session_id:
            self.session_delete_requested.emit(session_id)

    def _show_context_menu(self, pos) -> None:
        index = self.list_view.indexAt(pos)
        if not index.isValid():
            return
        if str(index.data(SessionListModel.KindRole) or "session") != "session":
            return

        session_id = self.model.session_id_at(index)
        if not session_id:
            return

        menu = QMenu(self.list_view)
        delete_action = menu.addAction("Delete chat")
        selected = menu.exec(self.list_view.viewport().mapToGlobal(pos))
        if selected is delete_action:
            self.session_delete_requested.emit(session_id)
