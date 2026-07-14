"""
Whispered - Centralized Theme System
Design tokens and global QSS generation.
"""

from dataclasses import dataclass

from core.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Theme:
    name: str
    # Backgrounds
    bg_base: str        # deepest background (window)
    bg_deep: str        # text areas, list backgrounds
    bg_surface: str     # panels, inputs
    bg_elevated: str    # buttons default
    # Borders
    border: str
    border_input: str
    border_hover: str
    # Accent
    accent: str
    accent_hover: str
    accent_pressed: str
    # Text
    text_primary: str
    text_secondary: str
    text_muted: str
    text_disabled: str
    # Semantic
    success: str
    warning: str
    error: str
    # Radii
    radius_sm: str
    radius_md: str
    radius_lg: str
    # Font sizes
    font_xs: str
    font_sm: str
    font_md: str
    font_lg: str


# ── Spacing scale (px) — use these instead of ad-hoc margin/padding numbers ──
SPACING_XS = 4
SPACING_SM = 8
SPACING_MD = 12
SPACING_LG = 16
SPACING_XL = 24

DARK = Theme(
    name="dark",
    bg_base="#1a1a1a",
    bg_deep="#1e1e1e",
    bg_surface="#2a2a2a",
    bg_elevated="#2d2d2d",
    border="#3a3a3a",
    border_input="#4a4a4a",
    border_hover="#5a5a5a",
    accent="#6366f1",
    accent_hover="#818cf8",
    accent_pressed="#4f46e5",
    text_primary="#e0e0e0",
    text_secondary="#888888",
    text_muted="#666666",
    text_disabled="#606060",
    success="#22c55e",
    warning="#f59e0b",
    error="#ef4444",
    radius_sm="3px",
    radius_md="6px",
    radius_lg="10px",
    font_xs="10px",
    font_sm="11px",
    font_md="12px",
    font_lg="14px",
)

LIGHT = Theme(
    name="light",
    bg_base="#f0f0f0",
    bg_deep="#ffffff",
    bg_surface="#e8e8e8",
    bg_elevated="#dcdcdc",
    border="#c0c0c0",
    border_input="#aaaaaa",
    border_hover="#888888",
    accent="#6366f1",
    accent_hover="#818cf8",
    accent_pressed="#4f46e5",
    text_primary="#1a1a1a",
    text_secondary="#555555",
    text_muted="#777777",
    text_disabled="#aaaaaa",
    success="#16a34a",
    warning="#d97706",
    error="#dc2626",
    radius_sm="3px",
    radius_md="6px",
    radius_lg="10px",
    font_xs="10px",
    font_sm="11px",
    font_md="12px",
    font_lg="14px",
)

THEMES: dict[str, Theme] = {"dark": DARK, "light": LIGHT}
_active_theme: Theme = DARK


def get_theme() -> Theme:
    return _active_theme


def build_stylesheet(t: Theme) -> str:
    return f"""
    /* ── Base ── */
    QWidget {{
        background-color: {t.bg_base};
        color: {t.text_primary};
        font-size: {t.font_md};
        selection-background-color: {t.accent};
        selection-color: #ffffff;
    }}
    QMainWindow, QDialog {{
        background-color: {t.bg_base};
    }}
    QLabel {{
        background: transparent;
        color: {t.text_primary};
        border: none;
    }}

    /* ── Buttons ── */
    QPushButton {{
        background-color: {t.bg_elevated};
        color: {t.text_primary};
        border: 1px solid {t.border};
        border-radius: {t.radius_md};
        padding: 8px 12px;
        font-size: {t.font_md};
    }}
    QPushButton:hover {{
        background-color: {t.bg_surface};
        border-color: {t.border_hover};
    }}
    QPushButton:pressed {{
        background-color: #4a4a4a;
    }}
    QPushButton:disabled {{
        background-color: #252525;
        color: {t.text_disabled};
        border-color: {t.border};
    }}

    QPushButton[variant="primary"] {{
        background-color: {t.accent};
        color: #ffffff;
        border: none;
        padding: 9px 20px;
        font-weight: bold;
    }}
    QPushButton[variant="primary"]:hover {{
        background-color: {t.accent_hover};
    }}
    QPushButton[variant="primary"]:pressed {{
        background-color: {t.accent_pressed};
    }}
    QPushButton[variant="primary"]:disabled {{
        background-color: {t.border};
        color: {t.text_disabled};
    }}

    QPushButton[variant="danger"] {{
        background-color: transparent;
        color: {t.text_secondary};
        border: 1px solid {t.border_input};
    }}
    QPushButton[variant="danger"]:hover {{
        background-color: rgba(248, 113, 113, 0.1);
        color: {t.error};
        border-color: {t.error};
    }}

    QPushButton[variant="ghost"] {{
        background: transparent;
        border: none;
        color: {t.text_secondary};
    }}
    QPushButton[variant="ghost"]:hover {{
        background-color: rgba(99, 102, 241, 0.1);
        color: {t.text_primary};
    }}

    QPushButton:checkable {{
        background: transparent;
        border: 1px solid {t.border_input};
        color: {t.text_secondary};
        padding: 4px 12px;
    }}
    QPushButton:checkable:checked {{
        border-color: {t.accent};
        color: {t.accent};
    }}
    QPushButton:checkable:hover {{
        background-color: rgba(99, 102, 241, 0.1);
    }}

    /* ── ComboBox ── */
    QComboBox {{
        padding: 6px 10px;
        padding-right: 25px;
        border: 1px solid {t.border};
        border-radius: {t.radius_md};
        background-color: {t.bg_surface};
        color: {t.text_primary};
        font-size: {t.font_md};
    }}
    QComboBox:hover {{
        border-color: {t.border_hover};
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: center right;
        width: 18px;
        border: none;
        background: transparent;
    }}
    QComboBox::down-arrow {{
        width: 0;
        height: 0;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 4px solid {t.text_secondary};
    }}
    QComboBox QAbstractItemView {{
        background-color: {t.bg_surface};
        border: 1px solid {t.border};
        selection-background-color: {t.accent};
        color: {t.text_primary};
        outline: none;
    }}

    /* ── Text inputs ── */
    QTextEdit {{
        background-color: {t.bg_deep};
        color: {t.text_primary};
        border: 1px solid {t.border};
        border-radius: 8px;
        padding: 12px;
        font-size: 13px;
    }}
    QLineEdit {{
        background-color: {t.bg_deep};
        color: {t.text_primary};
        border: 1px solid {t.border};
        border-radius: 4px;
        padding: 4px 6px;
        font-size: {t.font_sm};
    }}
    QLineEdit:focus {{
        border-color: {t.accent};
    }}

    /* ── Progress bar ── */
    QProgressBar {{
        border: none;
        border-radius: {t.radius_sm};
        background-color: {t.bg_surface};
        height: 4px;
        text-align: center;
        color: transparent;
    }}
    QProgressBar::chunk {{
        background-color: {t.accent};
        border-radius: {t.radius_sm};
    }}

    /* ── CheckBox ── */
    QCheckBox {{
        color: {t.text_secondary};
        font-size: {t.font_md};
        spacing: 8px;
        background: transparent;
    }}
    QCheckBox:checked {{
        color: {t.text_primary};
    }}
    QCheckBox::indicator {{
        width: 14px;
        height: 14px;
        border: 1px solid {t.border_input};
        border-radius: 3px;
        background-color: {t.bg_surface};
    }}
    QCheckBox::indicator:checked {{
        background-color: {t.accent};
        border-color: {t.accent};
    }}
    QCheckBox::indicator:hover {{
        border-color: {t.accent};
    }}

    /* ── Tabs ── */
    QTabWidget::pane {{
        border: none;
        background: transparent;
    }}
    QTabBar::tab {{
        background-color: {t.bg_surface};
        color: {t.text_secondary};
        padding: 10px 20px;
        border: none;
        border-top-left-radius: 8px;
        border-top-right-radius: 8px;
        margin-right: 2px;
        font-size: {t.font_md};
    }}
    QTabBar::tab:selected {{
        background-color: {t.bg_elevated};
        color: {t.text_primary};
    }}
    QTabBar::tab:hover:!selected {{
        background-color: {t.bg_elevated};
        color: {t.text_primary};
    }}

    /* ── List widgets ── */
    QListWidget {{
        background-color: {t.bg_deep};
        border: 1px solid {t.border};
        border-radius: {t.radius_md};
        padding: 4px;
        outline: none;
    }}
    QListWidget::item {{
        border-radius: 4px;
        padding: 4px;
    }}
    QListWidget::item:selected {{
        background-color: {t.bg_surface};
        color: {t.text_primary};
    }}
    QListWidget::item:hover {{
        background-color: {t.bg_elevated};
    }}

    /* ── Scrollbars ── */
    QScrollBar:vertical {{
        background: transparent;
        width: 6px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {t.border_hover};
        border-radius: 3px;
        min-height: 20px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {t.text_muted};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
        border: none;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 6px;
        margin: 0;
    }}
    QScrollBar::handle:horizontal {{
        background: {t.border_hover};
        border-radius: 3px;
        min-width: 20px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {t.text_muted};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0;
        border: none;
    }}

    /* ── Splitter ── */
    QSplitter::handle {{
        background: {t.border};
    }}
    QSplitter::handle:horizontal {{ width: 1px; }}
    QSplitter::handle:vertical {{ height: 1px; }}

    /* ── Scroll area ── */
    QScrollArea {{
        background: transparent;
        border: none;
    }}
    QScrollArea > QWidget > QWidget {{
        background: transparent;
    }}

    /* ── Tooltip ── */
    QToolTip {{
        background-color: {t.bg_elevated};
        color: {t.text_primary};
        border: 1px solid {t.border};
        border-radius: {t.radius_sm};
        padding: 4px 8px;
        font-size: {t.font_sm};
    }}

    /* ── Context menus ── */
    QMenu {{
        background-color: {t.bg_surface};
        border: 1px solid {t.border};
        border-radius: {t.radius_md};
        padding: 4px;
    }}
    QMenu::item {{
        padding: 6px 24px;
        border-radius: 4px;
    }}
    QMenu::item:selected {{
        background-color: {t.accent};
        color: #ffffff;
    }}
    QMenu::separator {{
        height: 1px;
        background: {t.border};
        margin: 4px 8px;
    }}

    /* ── Semantic roles (QLabel/QFrame/QWidget[role=...]) ──
       Prefer these over ad-hoc setStyleSheet() calls with literal colors:
       widget.setProperty("role", "muted") picks up the matching rule here. */
    QLabel[role="muted"] {{
        color: {t.text_secondary};
        background: transparent;
        border: none;
    }}
    QLabel[role="heading"] {{
        color: {t.text_secondary};
        font-weight: bold;
        font-size: {t.font_md};
        background: transparent;
        border: none;
    }}
    QLabel[role="title"] {{
        color: {t.text_primary};
        font-weight: bold;
        font-size: {t.font_lg};
        background: transparent;
        border: none;
    }}
    QLabel[role="danger-text"] {{
        color: {t.error};
        background: transparent;
        border: none;
    }}
    QLabel[role="success-text"] {{
        color: {t.success};
        background: transparent;
        border: none;
    }}
    QLabel[role="warning-text"] {{
        color: {t.warning};
        background: transparent;
        border: none;
    }}
    QFrame[role="divider"] {{
        background-color: {t.border};
        border: none;
    }}
    QWidget[role="card"] {{
        background-color: {t.bg_surface};
        border-radius: {t.radius_lg};
    }}
    QLabel[role="chip"] {{
        background-color: {t.bg_elevated};
        color: {t.text_secondary};
        border-radius: {t.radius_sm};
        padding: 2px 8px;
        font-size: {t.font_xs};
    }}
    QPushButton[role="accent-badge"] {{
        background-color: rgba(99, 102, 241, 0.2);
        border: none;
        border-radius: 12px;
        padding: 4px 12px;
        color: {t.accent};
        font-size: {t.font_sm};
    }}
    QPushButton[role="accent-badge"]:hover {{
        background-color: rgba(99, 102, 241, 0.3);
    }}

    /* ── Message boxes ── */
    QMessageBox {{
        background-color: {t.bg_surface};
    }}
    QMessageBox QLabel {{
        color: {t.text_primary};
    }}
    """


def apply_theme(app, name: str = "dark") -> Theme:
    """Apply a named theme to the application. Returns the active Theme."""
    global _active_theme
    t = THEMES.get(name, DARK)
    _active_theme = t
    try:
        app.setStyleSheet(build_stylesheet(t))
    except Exception as exc:
        logger.warning("Failed to apply theme %s: %s; falling back to qdarktheme", name, exc)
        try:
            import qdarktheme
            qdarktheme.setup_theme("dark", custom_colors={"primary": "#6366f1"})
        except Exception:
            pass
    return t
