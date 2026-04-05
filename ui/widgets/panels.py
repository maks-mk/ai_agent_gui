from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QScrollArea, QTabWidget, QTextBrowser, QVBoxLayout, QWidget

from ui.theme import ACCENT_BLUE, AMBER_WARNING, BORDER, MONO_FONT_FAMILY, SURFACE_CARD, TEXT_MUTED, TEXT_PRIMARY
from .foundation import _fa_icon


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
                    f"font-size: 8.8pt; font-family: '{MONO_FONT_FAMILY}';"
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



