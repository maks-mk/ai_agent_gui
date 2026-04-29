from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QFrame, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from .foundation import _fa_icon
from .tools import ToolCardWidget
from ui.theme import TEXT_MUTED


class ToolGroupWidget(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ToolGroupFrame")
        self.setFrameShape(QFrame.NoFrame)
        self._tools: list[ToolCardWidget] = []
        self._collapsed = False
        self._completed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(4)

        self.header_btn = QPushButton(self)
        self.header_btn.setObjectName("ToolGroupHeaderButton")
        self.header_btn.setCheckable(True)
        self.header_btn.setFlat(True)
        self.header_btn.setChecked(True)
        self.header_btn.setCursor(Qt.PointingHandCursor)
        self.header_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.header_btn.setMinimumWidth(0)
        self.header_btn.setIconSize(QSize(9, 9))
        self.header_btn.setAccessibleName("Tool results group")
        self.header_btn.setAccessibleDescription("Expand or collapse the tool results for this turn")
        self.header_btn.clicked.connect(self._toggle)
        layout.addWidget(self.header_btn)

        self.container = QWidget(self)
        self.container.setObjectName("ToolGroupContainer")
        self.inner = QVBoxLayout(self.container)
        self.inner.setContentsMargins(12, 0, 0, 0)
        self.inner.setSpacing(4)
        layout.addWidget(self.container)

        self._sync_header()

    def add_tool(self, card: ToolCardWidget) -> None:
        if card in self._tools:
            return
        self._tools.append(card)
        self.inner.addWidget(card)
        if self._completed:
            self._completed = False
            self.expand()
        else:
            self._sync_header()

    def collapse(self) -> None:
        self._completed = True
        if self._collapsed:
            self._sync_header()
            return
        self._collapsed = True
        self.container.hide()
        self.header_btn.setChecked(False)
        self._sync_header()

    def expand(self) -> None:
        self._collapsed = False
        self.container.show()
        self.header_btn.setChecked(True)
        self._sync_header()

    def _toggle(self, checked: bool = False) -> None:
        self._collapsed = not checked
        self.container.setVisible(checked)
        self._sync_header()

    def _sync_header(self) -> None:
        expanded = not self._collapsed
        self.header_btn.setIcon(
            _fa_icon("fa5s.caret-down" if expanded else "fa5s.caret-right", color=TEXT_MUTED, size=9)
        )
        if self._completed:
            self.header_btn.setText(f"Выполнено ({len(self._tools)} инструментов)")
            return
        self.header_btn.setText("Выполняется...")
