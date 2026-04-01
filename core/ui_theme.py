from __future__ import annotations

# --- Claude-like Dark Theme Colors (Чуть светлее) ---
ACCENT_BLUE = "#A1A1AA"
ACCENT_BLUE_SOFT = "#303036"   
TEXT_PRIMARY = "#E4E4E7"
TEXT_SECONDARY = "#A1A1AA"
TEXT_MUTED = "#82828C"
TEXT_DIM = "#5F5F66"

# Основные фоны (сделали светлее)
SURFACE_BG = "#212124"         # Главный фон окна (был #18181B)
SURFACE_CARD = "#2C2C30"       # Карточки, боковая панель и меню (был #27272A)
SURFACE_ALT = "#424248"        # Кнопки и выделения (был #3F3F46)
BORDER = "#424248"             # Границы элементов
SEPARATOR = "#2C2C30"          # Разделители

AMBER_WARNING = "#D97706"
ERROR_RED = "#EF4444"
SUCCESS_GREEN = "#10B981"
CODE_BG = "#141417"            # Фон для блоков кода (был #09090B)
CODE_TEXT = "#E4E4E7"

# Цвета поля ввода (Composer)
_COMPOSER_BG = "#2C2C30"       # Фон поля ввода (был #27272A)
_COMPOSER_BORDER = "#424248"   # Рамка поля ввода
_SEND_BTN_BG = "#E4E4E7"       
_SEND_BTN_HOVER = "#FFFFFF"
_SEND_BTN_DISABLED = "#424248"

APP_FONT_FAMILY = "Segoe UI"   
MONO_FONT_FAMILY = "Cascadia Mono"


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def blend_hex(start_hex: str, end_hex: str, factor: float) -> str:
    factor = max(0.0, min(1.0, factor))
    start = _hex_to_rgb(start_hex)
    end = _hex_to_rgb(end_hex)
    blended = tuple(
        round(start[index] + (end[index] - start[index]) * factor)
        for index in range(3)
    )
    return "#{:02X}{:02X}{:02X}".format(*blended)


def build_stylesheet() -> str:
    transcript_panel_bg = blend_hex(SURFACE_CARD, SURFACE_ALT, 0.18)
    transcript_panel_border = blend_hex(BORDER, "#FFFFFF", 0.08)
    transcript_panel_hover = blend_hex(SURFACE_CARD, SURFACE_ALT, 0.32)
    return f"""
    QWidget {{
        background: {SURFACE_BG};
        color: {TEXT_PRIMARY};
        font-family: "{APP_FONT_FAMILY}";
        font-size: 10pt;
    }}

    QMainWindow {{
        background: {SURFACE_BG};
    }}

    QFrame#SidebarCard,
    QFrame#StatusCard,
    QFrame#NoticeCard {{
        background: {SURFACE_CARD};
        border: 1px solid {BORDER};
        border-radius: 7px;
    }}

    QFrame#TranscriptMetaChip {{
        background: {transcript_panel_bg};
        border: 1px solid {transcript_panel_border};
        border-radius: 5px;
    }}

    QFrame#ApprovalCard {{
        background: {SURFACE_CARD};
        border: 1px solid {AMBER_WARNING};
        border-radius: 7px;
    }}

    QFrame#UserBubble {{
        background: {blend_hex(SURFACE_CARD, ACCENT_BLUE_SOFT, 0.32)};
        border: 1px solid {blend_hex(BORDER, ACCENT_BLUE, 0.18)};
        border-radius: 12px;
    }}

    QDialog#InfoPopup {{
        background: {SURFACE_CARD};
        border: 1px solid {BORDER};
        border-radius: 10px;
    }}

    QWidget#TranscriptContainer {{
        background: transparent;
    }}

    QFrame#TranscriptRow,
    QFrame#ToolRow,
    QFrame#FlatNoticeRow,
    QFrame#InlineStatusRow {{
        background: transparent;
        border: none;
    }}

    QLabel#SectionTitle {{
        color: {TEXT_PRIMARY};
        font-weight: 600;
        font-size: 12pt;
    }}

    QLabel#SidebarSectionTitle {{
        color: {blend_hex(TEXT_MUTED, TEXT_PRIMARY, 0.22)};
        font-weight: 600;
        font-size: 11pt;
        padding-left: 2px;
    }}

    QLabel#TranscriptRole {{
        color: {TEXT_MUTED};
        font-weight: normal;
        font-size: 9.5pt;
    }}

    QLabel#TranscriptBody {{
        color: {TEXT_PRIMARY};
        font-size: 11pt;
        background: transparent;
    }}

    QLabel#TranscriptMeta {{
        color: {TEXT_MUTED};
        font-size: 8.5pt;
    }}

    QLabel#MutedText,
    QLabel#MetaText {{
        color: {TEXT_MUTED};
    }}

    QLabel#StatusLabel {{
        color: {TEXT_PRIMARY};
        font-weight: 600;
    }}

    QToolButton#InlineStatusSpinner {{
        background: transparent;
        border: none;
        padding: 0px;
    }}

    QTabWidget::pane {{
        border: none;
        background: transparent;
    }}

    QTabBar::tab {{
        background: transparent;
        border: none;
        padding: 3px 6px;
        margin-right: 2px;
        color: {TEXT_MUTED};
        border-radius: 6px;
        font-size: 8.5pt;
    }}

    QTabBar::tab:selected {{
        background: {SURFACE_ALT};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER};
    }}

    QToolBar {{
        background: {SURFACE_CARD};
        border: 1px solid {BORDER};
        border-radius: 7px;
        spacing: 3px;
        padding: 1px 3px;
    }}

    QToolButton,
    QPushButton {{
        background: {SURFACE_ALT};
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding: 3px 6px;
        color: {TEXT_PRIMARY};
    }}

    QToolButton:hover,
    QPushButton:hover {{
        background: {blend_hex(SURFACE_ALT, ACCENT_BLUE_SOFT, 0.55)};
        border-color: {blend_hex(BORDER, ACCENT_BLUE, 0.4)};
    }}

    QToolButton:pressed,
    QPushButton:pressed {{
        background: {blend_hex(SURFACE_ALT, ACCENT_BLUE_SOFT, 0.8)};
    }}

    QPushButton#PrimaryButton {{
        background: {SURFACE_ALT};
        color: white;
        border-color: {blend_hex(BORDER, ACCENT_BLUE, 0.4)};
        font-weight: 600;
    }}

    QPushButton#DangerButton {{
        background: {ERROR_RED};
        color: white;
        border-color: {ERROR_RED};
        font-weight: 600;
    }}

    QPushButton:disabled,
    QToolButton:disabled {{
        color: {TEXT_DIM};
        background: {SURFACE_ALT};
        border-color: {BORDER};
    }}

    QToolButton#DisclosureButton,
    QPushButton#DisclosureButton {{
        background: transparent;
        border: none;
        border-radius: 4px;
        padding: 0px 2px;
        min-height: 18px;
        font-size: 9pt;
        color: {TEXT_MUTED};
        text-align: left;
    }}

    QToolButton#DisclosureButton:hover,
    QPushButton#DisclosureButton:hover {{
        background: transparent;
        color: {blend_hex(TEXT_MUTED, TEXT_PRIMARY, 0.45)};
    }}

    QPlainTextEdit,
    QTextBrowser,
    QListView,
    QListWidget,
    QTreeWidget {{
        background: {SURFACE_CARD};
        border: 1px solid {BORDER};
        border-radius: 7px;
        selection-background-color: {blend_hex(ACCENT_BLUE_SOFT, ACCENT_BLUE, 0.25)};
        selection-color: {TEXT_PRIMARY};
    }}

    QListView#SessionListView {{
        background: transparent;
        border: none;
        outline: none;
        padding: 0px;
    }}

    /* ---- Composer pill ---- */
    QFrame#ComposerPill {{
        background: {_COMPOSER_BG};
        border: 1px solid {_COMPOSER_BORDER};
        border-radius: 20px;
    }}

    QPlainTextEdit#ComposerEdit {{
        background: transparent;
        border: none;
        border-radius: 0px;
        padding: 6px 2px;
        font-size: 10.5pt;
    }}

    QPushButton#ComposerSendButton {{
        background: {_SEND_BTN_BG};
        color: #08090B;
        border: none;
        border-radius: 15px;
        font-weight: 700;
    }}

    QPushButton#ComposerSendButton:hover {{
        background: {_SEND_BTN_HOVER};
        border: none;
    }}

    QPushButton#ComposerSendButton:pressed {{
        background: #C8CACF;
        border: none;
    }}

    QPushButton#ComposerSendButton:disabled {{
        background: {_SEND_BTN_DISABLED};
        color: {TEXT_DIM};
        border: none;
    }}

    QToolButton#ComposerAttachButton {{
        background: transparent;
        border: 1px solid {_COMPOSER_BORDER};
        border-radius: 14px;
        padding: 0px;
    }}

    QTextBrowser#AssistantBody {{
        background: transparent;
        border: none;
        border-radius: 0px;
        padding: 0px;
        font-family: "{APP_FONT_FAMILY}";
        font-size: 11.5pt;
    }}

    QPushButton#ToolCallButton {{
        background: transparent;
        border: none;
        border-radius: 4px;
        padding: 0px;
        color: {TEXT_MUTED};
        font-size: 10.5pt;
        font-weight: 400;
        text-align: left;
    }}

    QPushButton#ToolCallButton:hover {{
        background: transparent;
        color: {blend_hex(TEXT_MUTED, TEXT_PRIMARY, 0.45)};
        border: none;
    }}

    QPushButton#ToolCallButton:checked {{
        background: transparent;
        color: {TEXT_PRIMARY};
        border: none;
    }}

    QPlainTextEdit#CodeView {{
        background: {transcript_panel_bg};
        color: {CODE_TEXT};
        border: 1px solid {transcript_panel_border};
        border-radius: 5px;
        font-family: "{MONO_FONT_FAMILY}";
        font-size: 9pt;
        padding: 6px;
    }}

    QPlainTextEdit#InlineCodeView {{
        background: {transcript_panel_bg};
        color: {CODE_TEXT};
        border: 1px solid {transcript_panel_border};
        border-radius: 5px;
        font-family: "{MONO_FONT_FAMILY}";
        font-size: 8.9pt;
        padding: 6px;
    }}

    QScrollArea {{
        border: none;
        background: transparent;
    }}

    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
        margin: 4px 0 4px 0;
    }}

    QScrollBar::handle:vertical {{
        background: {blend_hex(BORDER, TEXT_MUTED, 0.35)};
        border-radius: 4px;
        min-height: 20px;
    }}

    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical,
    QScrollBar::sub-page:vertical {{
        background: transparent;
        border: none;
        height: 0px;
    }}

    QMenuBar {{
        background: {SURFACE_BG};
        color: {TEXT_SECONDARY};
    }}

    QMenuBar::item {{
        background: transparent;
        padding: 4px 8px;
        border-radius: 6px;
    }}

    QMenu {{
        background: {SURFACE_CARD};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER};
        padding: 6px;
    }}

    QMenuBar::item:selected,
    QMenu::item:selected {{
        background: {ACCENT_BLUE_SOFT};
        border-radius: 6px;
    }}

    QStatusBar {{
        background: {blend_hex(SURFACE_BG, SURFACE_CARD, 0.28)};
        color: {TEXT_MUTED};
        border-top: 1px solid {BORDER};
        min-height: 22px;
        padding-left: 6px;
    }}

    QStatusBar::item {{
        border: none;
    }}

    QLabel#StatusBarMeta {{
        color: {blend_hex(TEXT_MUTED, TEXT_PRIMARY, 0.16)};
        font-size: 9pt;
        padding-right: 4px;
    }}

    QLabel#StatusBarState {{
        color: {blend_hex(TEXT_PRIMARY, TEXT_MUTED, 0.12)};
        font-size: 9.6pt;
        font-weight: 600;
        padding-left: 2px;
    }}
    """
