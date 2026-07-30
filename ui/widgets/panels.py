from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ui.theme import ACCENT_BLUE
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
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
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
    availability_changed = Signal(str, str, bool)

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
        self._pending_servers: dict[str, bool] = {}

        self.scroll.setWidget(self._container)
        root.addWidget(self.scroll)

    def set_tools(self, tools: list[dict[str, Any]]) -> None:
        while self._inner.count() > 1:
            item = self._inner.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        grouped: dict[str, list[dict[str, str]]] = {"Read-only": [], "Protected": [], "MCP": []}
        for row in tools:
            grouped.setdefault(row["group"], []).append(row)

        insert_pos = 0
        for group_name in ("Read-only", "Protected", "MCP"):
            items = grouped.get(group_name, [])
            if not items:
                continue

            header = QLabel(group_name.upper())
            header.setObjectName("ToolGroupHeader")
            header.setProperty("toolGroup", group_name.lower().replace("-", "_"))
            header.style().unpolish(header)
            header.style().polish(header)
            self._inner.insertWidget(insert_pos, header)
            insert_pos += 1

            for row in items:
                card = QFrame()
                card.setObjectName("ToolCard")
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(10, 8, 10, 8)
                card_layout.setSpacing(4)

                top_row = QHBoxLayout()
                top_row.setSpacing(6)

                name_button = QToolButton()
                name_button.setObjectName("ToolCardTitle")
                name_button.setText(row["name"])
                name_button.setCheckable(True)
                name_button.setChecked(False)
                name_button.setArrowType(Qt.RightArrow)
                name_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
                name_button.setAccessibleName(f"{row['name']} details")
                name_button.setToolTip(f"Show details for {row['name']}")
                top_row.addWidget(name_button)

                top_row.addStretch(1)

                if row.get("kind") in {"server", "tool"}:
                    toggle = QCheckBox()
                    toggle.setObjectName("ToolAvailabilitySwitch")
                    toggle.setFixedSize(34, 20)
                    toggle.setChecked(bool(row.get("enabled", True)))
                    toggle.setAccessibleName(f"{row['name']} enabled")
                    if row.get("kind") == "server":
                        name = str(row["name"])
                        pending = self._pending_servers.get(name)
                        toggle.setToolTip(f"Enable or disable MCP server {name}")
                        if pending is not None:
                            with QSignalBlocker(toggle):
                                toggle.setChecked(pending)
                            toggle.setEnabled(False)
                        toggle.toggled.connect(
                            lambda checked, switch=toggle, server_name=name:
                            self._request_server_change(switch, server_name, checked)
                        )
                    else:
                        toggle.setToolTip(f"Enable or disable tool {row['name']}")
                        toggle.toggled.connect(
                            lambda checked, name=str(row["name"]):
                            self.availability_changed.emit("tool", name, checked)
                        )
                    top_row.addWidget(toggle, 0, Qt.AlignRight | Qt.AlignVCenter)

                if row.get("kind") == "server" and str(row["name"]) in self._pending_servers:
                    pending_label = QLabel("Applying…")
                    pending_label.setObjectName("MCPServerLoadingLabel")
                    card_layout.addWidget(pending_label)

                card_layout.addLayout(top_row)

                details = QWidget()
                details.setObjectName("ToolCardDetails")
                details_layout = QVBoxLayout(details)
                details_layout.setContentsMargins(0, 0, 0, 0)
                details_layout.setSpacing(4)

                desc = row.get("description", "")
                if desc:
                    desc_label = QLabel(desc)
                    desc_label.setWordWrap(True)
                    desc_label.setObjectName("ToolCardDescription")
                    details_layout.addWidget(desc_label)

                for child in row.get("tools", []):
                    child_label = QLabel(f"  • {child.get('name', '')}\n    {child.get('description', '')}")
                    child_label.setWordWrap(True)
                    child_label.setObjectName("MCPToolCardItem")
                    child_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                    details_layout.addWidget(child_label)

                details.setVisible(False)
                name_button.toggled.connect(
                    lambda expanded, button=name_button, panel=details:
                    self._set_details_expanded(button, panel, expanded)
                )
                card_layout.addWidget(details)


                self._inner.insertWidget(insert_pos, card)
                insert_pos += 1

            sep = QFrame()
            sep.setFixedHeight(1)
            sep.setObjectName("ToolGroupSeparator")
            self._inner.insertWidget(insert_pos, sep)
            insert_pos += 1


    @staticmethod
    def _set_details_expanded(button: QToolButton, details: QWidget, expanded: bool) -> None:
        details.setVisible(expanded)
        button.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        button.setToolTip(
            f"{'Hide' if expanded else 'Show'} details for {button.text()}"
        )

    def _request_server_change(self, toggle: QCheckBox, name: str, enabled: bool) -> None:
        previous_enabled = not enabled
        self._pending_servers[name] = previous_enabled
        with QSignalBlocker(toggle):
            toggle.setChecked(previous_enabled)
        toggle.setEnabled(False)
        self.set_tools_pending_labels()
        self.availability_changed.emit("server", name, enabled)

    def set_tools_pending_labels(self) -> None:
        for card in self.findChildren(QFrame, "ToolCard"):
            title = card.findChild(QToolButton, "ToolCardTitle")
            if title is None or title.text() not in self._pending_servers:
                continue
            label = card.findChild(QLabel, "MCPServerLoadingLabel")
            if label is None:
                label = QLabel("Applying…", card)
                label.setObjectName("MCPServerLoadingLabel")
                card.layout().insertWidget(1, label)
            label.setText("Applying…")
            label.setToolTip("Waiting for the MCP runtime to finish reinitializing.")

    def clear_server_pending(self) -> None:
        self._pending_servers.clear()

    def fail_server_pending(self, message: str) -> None:
        for name, previous_enabled in self._pending_servers.items():
            for toggle in self.findChildren(QCheckBox, "ToolAvailabilitySwitch"):
                if toggle.accessibleName() != f"{name} enabled":
                    continue
                with QSignalBlocker(toggle):
                    toggle.setChecked(previous_enabled)
                toggle.setEnabled(True)
                toggle.setToolTip(f"MCP server change failed: {message}")
            for label in self.findChildren(QLabel, "MCPServerLoadingLabel"):
                parent = label.parentWidget()
                title = parent.findChild(QToolButton, "ToolCardTitle") if parent is not None else None
                if title is not None and title.text() == name:
                    label.setText("Failed to apply")
                    label.setToolTip(message)
        self._pending_servers.clear()


class InspectorPanelWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("InspectorPanel")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(6)

        title = QLabel("Inspector")
        title.setObjectName("InspectorSectionTitle")
        header_row.addWidget(title)
        header_row.addStretch(1)

        hint = QLabel("Run details, tools, and help")
        hint.setObjectName("InspectorMetaText")
        header_row.addWidget(hint, 0, Qt.AlignRight)
        layout.addLayout(header_row)

        self.tabs = QTabWidget()
        self.tabs.setAccessibleName("Inspector tabs")
        self.tabs.setAccessibleDescription("Switch between run details, tools, and help")
        self.overview_panel = OverviewPanelWidget()
        self.overview_panel.setAccessibleName("Run details")
        self.tools_panel = ToolsPanelWidget()
        self.tools_panel.setAccessibleName("Tools panel")

        help_widget = QWidget()
        help_layout = QVBoxLayout(help_widget)
        help_layout.setContentsMargins(0, 0, 0, 0)
        help_layout.setSpacing(0)
        self.help_text = QTextBrowser()
        self.help_text.setObjectName("InspectorHelpText")
        self.help_text.setOpenLinks(False)
        self.help_text.setOpenExternalLinks(False)
        self.help_text.setReadOnly(True)
        self.help_text.setAccessibleName("Help content")
        help_layout.addWidget(self.help_text)

        self.tabs.addTab(self.overview_panel, _fa_icon("fa5s.play-circle", color=ACCENT_BLUE, size=14), "Run")
        self.tabs.addTab(self.tools_panel, _fa_icon("fa5s.tools", color=ACCENT_BLUE, size=14), "Tools")
        self.tabs.addTab(help_widget, _fa_icon("fa5s.question-circle", color=ACCENT_BLUE, size=14), "Help")
        layout.addWidget(self.tabs, 1)


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

        inspector = InspectorPanelWidget()
        self.tabs = inspector.tabs
        self.overview_panel = inspector.overview_panel
        self.tools_panel = inspector.tools_panel
        self.help_text = inspector.help_text
        layout.addWidget(inspector)
