from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import qtawesome as qta
from PySide6.QtCore import QAbstractListModel, QModelIndex, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QLabel, QListView, QMenu, QStyle, QStyleOptionViewItem, QStyledItemDelegate, QVBoxLayout, QWidget

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


