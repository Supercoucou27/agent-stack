"""Color tokens, spacing, typography, and the global QSS stylesheet.

Centralizing the visual system here means the rest of the app can stay focused
on behavior.  The palette is a professional slate dark theme with a sky-blue
accent — dense, muted, and desktop-native.
"""
from __future__ import annotations

from dataclasses import dataclass


# --- Color tokens -------------------------------------------------------------
# Surface scale: deeper tones recede, lighter tones carry content.
BG_APP = "#0f1117"        # window background
BG_SIDEBAR = "#161922"    # left rail
BG_HEADER = "#1a1d26"     # top bar
BG_PANEL = "#1e222d"      # cards / panels
BG_PANEL_ALT = "#1a1e27"  # inspector / alternate panels
BG_RAISED = "#252a36"     # nested blocks, inputs, command preview
BG_INSET = "#0a0c10"      # logs, deepest inset

BORDER = "#2a2e3b"
BORDER_SOFT = "#1e2129"
BORDER_HOVER = "#3a3f4f"

FG_PRIMARY = "#e2e4e9"
FG_SECONDARY = "#9aa0b2"
FG_MUTED = "#6b7280"
FG_FAINT = "#4b515e"

ACCENT = "#0ea5e9"         # primary action (sky blue)
ACCENT_HOVER = "#38bdf8"
ACCENT_PRESSED = "#0284c7"
ACCENT_SOFT = "#0c4a6e"    # pill/chip backgrounds
ACCENT_DIM = "rgba(14, 165, 233, 0.12)"

SUCCESS = "#22c55e"
SUCCESS_SOFT = "#16a34a"
WARNING = "#f59e0b"
DANGER = "#ef4444"
DANGER_HOVER = "#dc2626"

SIDEBAR_WIDTH = 220
SIDEBAR_DEFAULT_WIDTH = 220
SIDEBAR_MIN_WIDTH = 160
SIDEBAR_COLLAPSED_WIDTH = 56
SPLITTER_HANDLE_WIDTH = 3
INSPECTOR_WIDTH = 320
INSPECTOR_DEFAULT_WIDTH = 320
INSPECTOR_MIN_WIDTH = 220
HEADER_HEIGHT = 48
SPLITTER_KEY = "llamaUI/splitter_sizes"


@dataclass(frozen=True)
class FontSpec:
    family: str
    base_px: int


FONT_UI = FontSpec("Inter, Segoe UI, Cantarell, sans-serif", 13)
FONT_MONO = FontSpec("JetBrains Mono, Consolas, monospace", 12)


def font_css(spec: FontSpec) -> str:
    return f'font-family: "{spec.family}"; font-size: {spec.base_px}px;'


# --- Stylesheet ---------------------------------------------------------------
# Built at call time; the app sets it via QApplication.setStyleSheet.


def build_stylesheet() -> str:
    f_ui = font_css(FONT_UI)
    f_mono = font_css(FONT_MONO)

    return f"""
    QWidget {{
        {f_ui}
        color: {FG_PRIMARY};
        background-color: {BG_APP};
    }}

    QMainWindow {{
        background-color: {BG_APP};
    }}

    /* --- Sidebar --- */
    QFrame#Sidebar {{
        background-color: {BG_SIDEBAR};
        border-right: 1px solid {BORDER_SOFT};
    }}
    QLabel#SidebarBrand {{
        color: {ACCENT};
        font-size: 16px;
        font-weight: 700;
        padding: 14px 20px 10px 20px;
        letter-spacing: 0.3px;
    }}
    QLabel#SidebarBrand[collapsed="true"] {{
        padding: 14px 0 10px 0;
        font-size: 15px;
        text-align: center;
    }}
    QPushButton#NavItem {{
        text-align: left;
        padding: 8px 20px;
        border: none;
        background: transparent;
        color: {FG_SECONDARY};
        font-size: 13px;
        border-left: 3px solid transparent;
    }}
    QPushButton#NavItem:hover {{
        background-color: {ACCENT_DIM};
        color: {FG_PRIMARY};
    }}
    QPushButton#NavItem:checked {{
        color: {FG_PRIMARY};
        font-weight: 600;
        background-color: rgba(14, 165, 233, 0.15);
        border-left: 3px solid {ACCENT};
    }}
    QPushButton#NavItem[collapsed="true"] {{
        text-align: center;
        padding: 10px 0;
        border-left: none;
        border-bottom: 2px solid transparent;
        font-size: 14px;
        font-weight: 600;
    }}
    QPushButton#NavItem[collapsed="true"]:checked {{
        border-left: none;
        border-bottom: 2px solid {ACCENT};
    }}
    QPushButton#SidebarToggle {{
        border: none;
        background: transparent;
        color: {FG_SECONDARY};
        font-size: 16px;
        padding: 10px 6px;
        border-radius: 4px;
    }}
    QPushButton#SidebarToggle:hover {{
        color: {FG_PRIMARY};
        background-color: {ACCENT_DIM};
    }}
    QPushButton#SidebarToggle[collapsed="true"] {{
        color: {ACCENT};
        font-size: 18px;
        padding: 12px 4px;
        background-color: {BG_RAISED};
        border: 1px solid {BORDER};
    }}
    QPushButton#SidebarToggle[collapsed="true"]:hover {{
        color: {ACCENT_HOVER};
        background-color: {ACCENT_DIM};
    }}
    QFrame#SidebarStatus {{
        background-color: {BG_PANEL_ALT};
        border-top: 1px solid {BORDER_SOFT};
    }}
    QLabel#SidebarStatusTitle {{
        color: {FG_PRIMARY};
        font-weight: 600;
        font-size: 13px;
    }}
    QLabel#SidebarStatusChip {{
        color: {FG_SECONDARY};
        background-color: {BG_INSET};
        border: 1px solid {BORDER_SOFT};
        border-radius: 4px;
        padding: 2px 6px;
        font-size: 11px;
    }}
    /* Splitter handle */
    QSplitter::handle:horizontal {{
        background-color: {BORDER_SOFT};
        width: {SPLITTER_HANDLE_WIDTH}px;
    }}
    QSplitter::handle:horizontal:hover {{
        background-color: {ACCENT};
    }}
    /* --- Header bar --- */
    QFrame#Header {{
        background-color: {BG_HEADER};
        border-bottom: 1px solid {BORDER_SOFT};
    }}
    QLabel#HeaderTitle {{
        color: {FG_PRIMARY};
        font-size: 14px;
        font-weight: 700;
        padding-left: 16px;
    }}
    QLabel#HeaderSubtitle {{
        color: {FG_MUTED};
        font-size: 11px;
        padding-left: 16px;
    }}

    /* --- Cards / panels --- */
    QFrame#Card {{
        background-color: {BG_PANEL};
        border: 1px solid {BORDER};
        border-radius: 8px;
    }}
    QFrame#CardAlt {{
        background-color: {BG_PANEL_ALT};
        border: 1px solid {BORDER};
        border-radius: 8px;
    }}
    QFrame#Inset {{
        background-color: {BG_INSET};
        border: 1px solid {BORDER};
        border-radius: 6px;
    }}
    QFrame#InsetRaised {{
        background-color: {BG_RAISED};
        border: 1px solid {BORDER};
        border-radius: 6px;
    }}
    QLabel#CardTitle {{
        color: {FG_PRIMARY};
        font-size: 14px;
        font-weight: 600;
    }}
    QLabel#CardSubtitle {{
        color: {FG_MUTED};
        font-size: 11px;
    }}
    QLabel#FieldLabel {{
        color: {FG_SECONDARY};
        font-size: 11px;
        font-weight: 500;
    }}
    QLabel#FieldValue {{
        color: {FG_PRIMARY};
        font-size: 16px;
        font-weight: 700;
    }}
    QLabel#Muted {{
        color: {FG_MUTED};
    }}
    QLabel#Mono {{
        {f_mono}
        color: {FG_MUTED};
    }}

    /* --- Inspector chips --- */
    QLabel#Chip {{
        color: {FG_PRIMARY};
        font-size: 11px;
        font-weight: 600;
        padding: 2px 8px;
        border-radius: 4px;
    }}
    QLabel#ChipSuccess {{ background-color: rgba(34, 197, 94, 0.18); color: {SUCCESS}; }}
    QLabel#ChipWarning {{ background-color: rgba(245, 158, 11, 0.18); color: {WARNING}; }}
    QLabel#ChipAccent  {{ background-color: {ACCENT_SOFT}; color: {FG_PRIMARY}; }}
    QLabel#ChipMuted   {{ background-color: {BG_RAISED}; color: {FG_SECONDARY}; }}

    /* --- Buttons --- */
    QPushButton {{
        background-color: {ACCENT};
        color: white;
        border: none;
        border-radius: 6px;
        padding: 6px 14px;
        font-size: 12px;
        font-weight: 500;
        min-width: 72px;
    }}
    QPushButton:hover {{ background-color: {ACCENT_HOVER}; }}
    QPushButton:pressed {{ background-color: {ACCENT_PRESSED}; }}
    QPushButton:disabled {{ background-color: {BG_RAISED}; color: {FG_FAINT}; }}

    QPushButton[variant="secondary"] {{
        background-color: {BG_RAISED};
        color: {FG_SECONDARY};
        border: 1px solid {BORDER};
    }}
    QPushButton[variant="secondary"]:hover {{
        background-color: {BORDER_HOVER};
        color: {FG_PRIMARY};
    }}
    QPushButton[variant="danger"] {{
        background-color: {DANGER};
    }}
    QPushButton[variant="danger"]:hover {{
        background-color: {DANGER_HOVER};
    }}
    QPushButton[variant="success"] {{
        background-color: {SUCCESS_SOFT};
    }}
    QPushButton[variant="success"]:hover {{
        background-color: #15803d;
    }}

    /* --- Inputs --- */
    QLineEdit {{
        background-color: {BG_RAISED};
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding: 6px 10px;
        color: {FG_PRIMARY};
        font-size: 13px;
        selection-background-color: {ACCENT};
        min-width: 120px;
    }}
    QLineEdit:focus {{ border: 1px solid {ACCENT}; }}

    QComboBox {{
        background-color: {BG_RAISED};
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding: 4px 10px;
        color: {FG_PRIMARY};
        font-size: 13px;
        min-width: 120px;
        min-height: 28px;
    }}
    QComboBox:hover {{ border: 1px solid {ACCENT}; }}
    QComboBox::drop-down {{
        border: none;
        width: 24px;
    }}
    QComboBox::down-arrow {{
        image: none;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 5px solid {FG_MUTED};
        margin-right: 6px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {BG_PANEL};
        border: 1px solid {BORDER};
        color: {FG_PRIMARY};
        selection-background-color: {ACCENT};
        selection-color: {FG_PRIMARY};
        outline: none;
        border-radius: 4px;
    }}

    QSpinBox, QDoubleSpinBox {{
        background-color: {BG_RAISED};
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding: 4px 8px;
        color: {FG_PRIMARY};
        font-size: 13px;
        min-width: 100px;
    }}
    QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {ACCENT}; }}
    QSpinBox::up-button, QSpinBox::down-button,
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
        background: transparent;
        border: none;
        width: 16px;
    }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
        image: none;
        border-left: 3px solid transparent;
        border-right: 3px solid transparent;
        border-bottom: 4px solid {FG_MUTED};
    }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
        image: none;
        border-left: 3px solid transparent;
        border-right: 3px solid transparent;
        border-top: 4px solid {FG_MUTED};
    }}

    QPlainTextEdit, QTextBrowser {{
        {f_mono}
        background-color: {BG_INSET};
        border: 1px solid {BORDER};
        border-radius: 6px;
        color: {FG_SECONDARY};
        padding: 8px;
        font-size: 12px;
        selection-background-color: {ACCENT};
    }}

    /* --- Tables --- */
    QTableWidget {{
        background-color: {BG_INSET};
        border: 1px solid {BORDER};
        border-radius: 6px;
        gridline-color: {BORDER_SOFT};
        selection-background-color: rgba(14, 165, 233, 0.22);
        selection-color: {FG_PRIMARY};
        font-size: 13px;
    }}
    QTableWidget::item {{
        padding: 4px 8px;
    }}
    QTableWidget::item:selected {{
        background-color: {ACCENT};
    }}
    QHeaderView::section {{
        background-color: {BG_RAISED};
        color: {FG_MUTED};
        padding: 6px 10px;
        border: none;
        border-right: 1px solid {BORDER_SOFT};
        border-bottom: 1px solid {BORDER_SOFT};
        font-size: 11px;
        font-weight: 600;
    }}

    /* --- Filter pills --- */
    QPushButton#FilterPill {{
        background-color: {BG_RAISED};
        color: {FG_SECONDARY};
        border-radius: 12px;
        padding: 4px 10px;
        font-size: 11px;
        border: 1px solid {BORDER};
    }}
    QPushButton#FilterPill:checked {{
        background-color: {ACCENT_SOFT};
        color: {FG_PRIMARY};
        border-color: {ACCENT};
    }}

    /* --- Scrollbars --- */
    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 8px;
    }}
    QScrollBar::handle {{
        background: {BORDER};
        border-radius: 4px;
        min-height: 24px;
    }}
    QScrollBar::handle:hover {{
        background: {BORDER_HOVER};
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{
        width: 0;
        height: 0;
    }}
    /* --- Slider --- */
    QSlider::groove:horizontal {{
        background: {BG_RAISED};
        border: 1px solid {BORDER};
        height: 4px;
        border-radius: 2px;
    }}
    QSlider::sub-page:horizontal {{
        background: {ACCENT};
        border: 1px solid {ACCENT};
        height: 4px;
        border-radius: 2px;
    }}
    QSlider::add-page:horizontal {{
        background: {BG_RAISED};
        border: 1px solid {BORDER};
        height: 4px;
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: {FG_PRIMARY};
        border: 1px solid {BORDER};
        width: 12px;
        height: 12px;
        margin: -5px 0;
        border-radius: 6px;
    }}
    QSlider::handle:horizontal:hover {{
        background: {ACCENT_HOVER};
        border-color: {ACCENT_HOVER};
    }}
    /* --- Tooltip --- */
    QToolTip {{
        background-color: {BG_RAISED};
        color: {FG_PRIMARY};
        border: 1px solid {BORDER};
        border-radius: 4px;
        padding: 4px 8px;
        font-size: 11px;
    }}

    /* --- Top page header --- */
    QFrame#TopHeader {{
        background-color: {BG_HEADER};
        border-bottom: 1px solid {BORDER_SOFT};
    }}
    QLabel#PageTitle {{
        color: {FG_PRIMARY};
        font-size: 18px;
        font-weight: 600;
        letter-spacing: 0.1px;
    }}
    QFrame#CenterColumn {{
        background-color: {BG_APP};
    }}
    QWidget#PageBody {{
        background-color: {BG_APP};
    }}

    /* --- Dashboard page --- */
    QWidget#DashboardChart {{
        background-color: {BG_INSET};
        border: 1px solid {BORDER};
        border-radius: 6px;
    }}
    QPlainTextEdit#DashboardLogDisplay {{
        {f_mono}
        background-color: {BG_INSET};
        border: 1px solid {BORDER};
        border-radius: 6px;
        color: {FG_SECONDARY};
        padding: 8px;
        font-size: 12px;
        selection-background-color: {ACCENT};
    }}

    /* --- Inspector collapse/expand buttons --- */
    QPushButton#InspectorCollapseBtn,
    QPushButton#InspectorExpandBtn {{
        background-color: transparent;
        border: none;
        color: {FG_MUTED};
        font-size: 14px;
        padding: 0px;
        min-width: 24px;
    }}
    QPushButton#InspectorCollapseBtn:hover,
    QPushButton#InspectorExpandBtn:hover {{
        color: {FG_PRIMARY};
        background-color: {ACCENT_DIM};
        border-radius: 4px;
    }}

    /* --- Splitter handle --- */
    QSplitter::handle {{
        background-color: {BORDER_SOFT};
    }}

    /* --- Importance labels --- */
    QLabel[important="1"] {{ color: {WARNING}; font-weight: 600; }}
    QLabel[important="2"] {{ color: {DANGER}; font-weight: 600; }}

    /* --- Model picker --- */
    QComboBox#ModelPicker {{
        min-width: 280px;
    }}

    /* --- CollapsibleGroup --- */
    #CollapsibleGroupHeader {{
        background: {BG_RAISED};
        border-radius: 6px;
        padding: 6px 10px;
    }}
    #CollapsibleGroupHeader:hover {{
        background: {BORDER_HOVER};
    }}
    #CollapsibleGroupTitle {{
        font-weight: 600;
        font-size: 13px;
    }}
    #CollapsibleGroupBody {{
        border: 1px solid {BORDER};
        border-top: none;
        border-radius: 0 0 6px 6px;
    }}
    #CollapsibleGroupCaret {{
        color: {FG_MUTED};
        min-width: 14px;
        font-size: 12px;
    }}
    #OptionCard {{
        background: {BG_PANEL};
        border: 1px solid {BORDER};
        border-radius: 6px;
    }}
    #OptionCard:hover {{
        border-color: {BORDER_HOVER};
    }}
    #OptionCardLabel {{
        font-weight: 500;
        font-size: 12px;
    }}
    #OptionCardFlag {{
        color: {FG_MUTED};
        font-size: 11px;
    }}
    #OptionCardChangedDot {{
        color: {DANGER};
        font-size: 12px;
    }}
    #ArgumentSearchBox {{
        min-width: 200px;
    }}
    QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 0 0 6px 6px; }}
    QTabBar::tab {{ padding: 6px 12px; font-size: 12px; }}
    QTabBar::tab:selected {{ background: {BG_RAISED}; border-bottom: 2px solid {ACCENT}; color: {FG_PRIMARY}; }}
    QTabBar::tab:!selected {{ color: {FG_MUTED}; }}
    /* Wrapped tab bar (replaces QTabWidget for advanced groups) */
    #WrappedTabBar {{ background: transparent; }}
    #WrappedTabBtn {{
        border: 1px solid transparent;
        border-radius: 4px;
        padding: 5px 12px;
        font-size: 12px;
        color: {FG_MUTED};
        background: transparent;
    }}
    #WrappedTabBtn:checked {{
        color: {FG_PRIMARY};
        background: {BG_RAISED};
        border-bottom: 2px solid {ACCENT};
    }}
    #WrappedTabBtn:hover {{ color: {FG_SECONDARY}; }}
    #WrappedTabStack {{ border: 1px solid {BORDER}; border-radius: 0 0 6px 6px; }}
    #AdvancedToggleBtn {{
        text-align: left;
        font-weight: 600;
        color: {FG_PRIMARY};
        font-size: 13px;
        padding: 4px 0;
        border: none;
        background: transparent;
        min-width: 0;
    }}
    #AdvancedToggleBtn:hover {{ color: {FG_SECONDARY}; }}
    """


def apply_palette(app) -> None:
    """Set a dark palette that matches the QSS for native-rendered widgets.

    QSS does not style every native surface (e.g. focus, tooltips, menus),
    so we set the default palette first and let QSS override per-control.
    """
    from PySide6.QtGui import QColor, QPalette

    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(BG_APP))
    p.setColor(QPalette.ColorRole.WindowText, QColor(FG_PRIMARY))
    p.setColor(QPalette.ColorRole.Base, QColor(BG_RAISED))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(BG_PANEL))
    p.setColor(QPalette.ColorRole.Text, QColor(FG_PRIMARY))
    p.setColor(QPalette.ColorRole.Button, QColor(BG_PANEL))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(FG_PRIMARY))
    p.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(FG_PRIMARY))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(FG_MUTED))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(BG_RAISED))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(FG_PRIMARY))
    app.setPalette(p)
