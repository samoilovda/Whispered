"""
Whispered - Centralized Theme System
Design tokens and global QSS generation.
"""

from dataclasses import dataclass

from core.logger import get_logger

logger = get_logger(__name__)

# Shared layout scale. UI modules should use these values instead of growing
# their own collection of almost-identical margins.
SPACE_1 = 4
SPACE_2 = 8
SPACE_3 = 12
SPACE_4 = 16
SPACE_5 = 20
SPACE_6 = 24
SPACE_8 = 32

# Categorical palette for distinguishing speakers in the transcript view.
# Same 8 colors regardless of light/dark theme (all mid-brightness/saturated
# enough to read on either background) — a content palette, not UI chrome.
SPEAKER_PALETTE: list[str] = [
    "#6366f1", "#22c55e", "#f59e0b", "#ef4444",
    "#06b6d4", "#ec4899", "#84cc16", "#8b5cf6",
]


class IconColors:
    """Standard icon colors, used as default fill for SVG icons (ui/icons.py)
    and passed explicitly wherever an icon needs a specific tint.

    Most of these are theme-independent by design (matches
    Theme.accent/success/warning, which are also identical between DARK
    and LIGHT — small saturated accent colors read fine on either
    background). DEFAULT/MUTED are the exception: they're plain grays
    meant to blend with the page's own text, so they resolve from the
    active Theme's text_secondary/text_muted (methods, not constants,
    since a plain class attribute is fixed at import time and can't
    track a later theme switch). DARK's text_secondary/text_muted are
    byte-identical to the old hardcoded values, so dark theme is
    unaffected; LIGHT's are the WCAG-contrast-checked shades already
    used for label text elsewhere.
    """
    PRIMARY = '#6366f1'   # Indigo (== Theme.accent)
    SUCCESS = '#22c55e'   # Green (== Theme.success, dark theme)
    WARNING = '#f59e0b'   # Amber (== Theme.warning, dark theme)
    ERROR = '#f87171'     # Red — deliberately lighter than Theme.error for icon visibility
    WHITE = '#ffffff'

    @staticmethod
    def default() -> str:
        return get_theme().text_secondary

    @staticmethod
    def muted() -> str:
        return get_theme().text_muted


# Colors for the stepped ProgressTimeline widget (a custom QPainter widget,
# not styled via QSS/roles). bg/fg/fg_active/separator are the page-relative
# tones (the neutral "pending" chevron, meant to blend with the page like
# any other muted UI chrome) and vary by theme via get_timeline_colors().
# bg_active/bg_done/bg_error/progress_active are saturated status-badge
# colors that stay legible against either page background on their own, so
# they're shared between both variants rather than duplicated.
_TIMELINE_STATUS_COLORS = {
    "bg_active": "#3730a3",
    "bg_done": "#166534",
    "bg_error": "#7f1d1d",
    "progress_active": "#6366f1",
}

_DARK_TIMELINE_COLORS = {
    **_TIMELINE_STATUS_COLORS,
    "bg": "#2a2a2a",
    "fg": "#888888",
    "fg_active": "#e0e0e0",
    "separator": "#1a1a1a",
}

_LIGHT_TIMELINE_COLORS = {
    **_TIMELINE_STATUS_COLORS,
    "bg": "#e8e8e8",
    "fg": "#555555",
    "fg_active": "#ffffff",
    "separator": "#ffffff",
}


def get_timeline_colors() -> dict[str, str]:
    """ProgressTimeline's palette for the active theme (see get_theme())."""
    return _LIGHT_TIMELINE_COLORS if get_theme().name == "light" else _DARK_TIMELINE_COLORS


@dataclass
class Theme:
    name: str
    # Backgrounds
    bg_base: str        # deepest background (window)
    bg_deep: str        # text areas, list backgrounds
    bg_surface: str     # panels, inputs
    bg_elevated: str    # buttons default
    bg_pressed: str     # button press flash
    bg_disabled: str    # disabled control fill
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
    info: str
    # Radii
    radius_sm: str
    radius_md: str
    radius_lg: str
    # Font sizes
    font_xs: str
    font_sm: str
    font_md: str
    font_lg: str


DARK = Theme(
    name="dark",
    bg_base="#1a1a1a",
    bg_deep="#1e1e1e",
    bg_surface="#2a2a2a",
    bg_elevated="#2d2d2d",
    bg_pressed="#4a4a4a",
    bg_disabled="#252525",
    border="#2e2e2e",
    border_input="#3a3a3a",
    border_hover="#4a4a4a",
    accent="#6366f1",
    accent_hover="#818cf8",
    accent_pressed="#4f46e5",
    text_primary="#e0e0e0",
    text_secondary="#888888",
    text_muted="#666666",
    # #606060 on bg_disabled #252525 measured at ~2.4:1 — the disabled
    # Apply button read as nearly blank, not just "muted". #707070 clears
    # ~3:1, legible while still visibly non-interactive.
    text_disabled="#707070",
    success="#22c55e",
    warning="#f59e0b",
    error="#ef4444",
    info="#38bdf8",
    radius_sm="6px",
    radius_md="10px",
    radius_lg="16px",
    font_xs="11px",
    font_sm="12px",
    font_md="13px",
    font_lg="15px",
)

LIGHT = Theme(
    name="light",
    bg_base="#f8f9fa",
    bg_deep="#f1f3f4",
    bg_surface="#ffffff",
    bg_elevated="#ffffff",
    bg_pressed="#f1f3f4",
    bg_disabled="#f8f9fa",
    border="#e8eaed",
    border_input="#8b9096",
    border_hover="#6f757b",
    accent="#6c4cf1",
    accent_hover="#7454f2",
    accent_pressed="#5930c9",
    text_primary="#202124",
    text_secondary="#5f6368",
    text_muted="#646a70",
    text_disabled="#767b80",
    success="#1e8e3e",
    warning="#f9ab00",
    error="#d93025",
    info="#1a73e8",
    radius_sm="6px",
    radius_md="12px",
    radius_lg="16px",
    font_xs="11px",
    font_sm="12px",
    font_md="13px",
    font_lg="15px",
)

THEMES: dict[str, Theme] = {"dark": DARK, "light": LIGHT}
_active_theme: Theme = DARK


def get_theme() -> Theme:
    return _active_theme


def mark_elides(widget, full_text: str) -> None:
    """Flag *widget* as deliberately eliding its own text.

    tools/render_ui_gallery.py's ``--check`` treats any visible text
    widget narrower than its ``sizeHint()`` as a clipping defect, with one
    exception: a widget that chose to elide (``QFontMetrics.elidedText``)
    and expose the full text via tooltip instead. Call this once you've
    set both the elided display text and the tooltip, so the gallery
    check and a human reading the code agree on which widgets are exempt.
    """
    widget.setProperty("_elides", True)
    widget.setToolTip(full_text)


def set_role(widget, role: str) -> None:
    """Set a widget's `role` property and force Qt to re-evaluate the
    matching QSS selector immediately.

    Needed any time a role is changed *after* the widget was already shown
    once (e.g. a status dot flipping between "muted"/"success-text"/
    "danger-text" at runtime) — a plain setProperty() alone only affects
    the next full stylesheet polish, which doesn't happen on its own.
    For a role set once at construction time, setProperty() alone is
    sufficient and the unpolish/polish call is a harmless no-op.
    """
    widget.setProperty("role", role)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def rgba(hex_color: str, alpha: float) -> str:
    """Convert a '#rrggbb' token to an 'rgba(r, g, b, a)' QSS literal.

    Lets badge backgrounds tint a theme color (e.g. accent at 20% opacity)
    without a second hardcoded hex literal per theme.
    """
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alpha})"


def build_stylesheet(t: Theme) -> str:
    # Deferred import: ui.icons imports IconColors from this module at load
    # time, so importing it back at module scope here would be circular —
    # safe by the time build_stylesheet() actually runs (theme applied
    # after the app's widgets/icons are already importable).
    from ui.icons import get_icon_file
    check_icon = get_icon_file("check", "#ffffff", 10)
    return f"""
    /* ── Base ── */
    QWidget {{
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI Variable", "Segoe UI", "Inter", "Roboto", "Helvetica Neue", sans-serif;
        background-color: {t.bg_base};
        color: {t.text_primary};
        font-size: {t.font_md};
        font-weight: 400;
        selection-background-color: {t.accent};
        selection-color: #ffffff;
    }}
    /* Transparent QMainWindow so sidebar and content extend to window top */
    QMainWindow {{
        background-color: transparent;
    }}
    QDialog, QMenu, QToolTip {{
        background-color: {t.bg_base};
    }}
    QWidget[role="content-area"] {{
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
        background-color: {t.bg_pressed};
    }}
    QPushButton:disabled {{
        background-color: {t.bg_disabled};
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

    QPushButton[role="checkable-success"]:checkable:checked {{
        border-color: {t.success};
        color: {t.success};
    }}
    QPushButton[role="checkable-success"]:checkable:hover {{
        background-color: {rgba(t.success, 0.1)};
    }}

    /* ── Sidebar nav buttons (QToolButton) ── */
    QToolButton[role="nav-button"] {{
        background: transparent;
        border: none;
        border-radius: {t.radius_md};
        color: {t.text_secondary};
    }}
    QToolButton[role="nav-button"]:hover {{
        background-color: {t.bg_surface};
        color: {t.text_primary};
    }}
    QToolButton[role="nav-button"]:checked {{
        background-color: {rgba(t.accent, 0.15)};
        color: {t.accent};
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
        font-size: {t.font_sm};
    }}
    QPlainTextEdit {{
        background-color: {t.bg_base};
        color: {t.text_primary};
        border: 1px solid {t.border};
        border-radius: {t.radius_sm};
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
        image: url({check_icon});
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
        background-color: transparent;
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
        font-weight: 500;
        font-size: {t.font_md};
        background: transparent;
        border: none;
    }}
    QLabel[role="title"] {{
        color: {t.text_primary};
        font-weight: 600;
        font-size: {t.font_lg};
        background: transparent;
        border: none;
    }}
    QLabel[role="page-title"] {{
        color: {t.text_primary};
        font-weight: 600;
        font-size: 18px;
        background: transparent;
        border: none;
    }}
    QLabel[role="section-title"] {{
        color: {t.text_primary};
        font-weight: 500;
        font-size: {t.font_lg};
        background: transparent;
        border: none;
    }}
    QLabel[size="small"] {{
        font-size: {t.font_xs};
    }}
    QLabel[role="danger-text"] {{
        color: {t.error};
        background: transparent;
        border: none;
    }}
    QPushButton[role="danger-text"] {{
        color: {t.error};
    }}
    QPushButton[role="warning-text"] {{
        color: {t.warning};
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
    QLabel[role="dim"] {{
        color: {t.text_muted};
        background: transparent;
        border: none;
    }}
    QLabel[role="divider-text"] {{
        color: {t.border};
        background: transparent;
        border: none;
    }}
    QFrame[role="divider"] {{
        background-color: {t.border};
        border: none;
    }}
    QPushButton[role="timestamp-link"] {{
        color: {t.accent};
        background: transparent;
        border: none;
        font-family: monospace;
        padding: 0;
    }}
    QPushButton[role="timestamp-link"]:hover {{
        color: {t.accent_hover};
        text-decoration: underline;
    }}
    QWidget[role="card"] {{
        background-color: {t.bg_surface};
        border-radius: {t.radius_lg};
    }}
    QWidget[role="form-section"] {{
        background-color: {t.bg_surface};
        border: 1px solid {t.border};
        border-radius: {t.radius_lg};
    }}
    QWidget[role="operation-bar"] {{
        background-color: transparent;
        border-top: none;
    }}
    QWidget[role="inline-banner"] {{
        background-color: {rgba(t.info, 0.10)};
        border: 1px solid {rgba(t.info, 0.35)};
        border-radius: {t.radius_md};
    }}
    QWidget[role="inline-banner-warning"] {{
        background-color: {rgba(t.warning, 0.10)};
        border: 1px solid {rgba(t.warning, 0.35)};
        border-radius: {t.radius_md};
    }}
    QWidget[role="inline-banner-error"] {{
        background-color: {rgba(t.error, 0.10)};
        border: 1px solid {rgba(t.error, 0.35)};
        border-radius: {t.radius_md};
    }}
    QWidget[role="inline-banner-success"] {{
        background-color: {rgba(t.success, 0.10)};
        border: 1px solid {rgba(t.success, 0.35)};
        border-radius: {t.radius_md};
    }}
    QLabel[role="status-neutral"], QLabel[role="status-info"],
    QLabel[role="status-success"], QLabel[role="status-warning"],
    QLabel[role="status-error"] {{
        border-radius: 10px;
        padding: 3px 8px;
        font-size: {t.font_xs};
        font-weight: 600;
    }}
    QLabel[role="status-neutral"] {{
        background-color: {rgba(t.text_secondary, 0.14)};
        color: {t.text_secondary};
    }}
    QLabel[role="status-info"] {{
        background-color: {rgba(t.info, 0.14)};
        color: {t.info};
    }}
    QLabel[role="status-success"] {{
        background-color: {rgba(t.success, 0.14)};
        color: {t.success};
    }}
    QLabel[role="status-warning"] {{
        background-color: {rgba(t.warning, 0.14)};
        color: {t.warning};
    }}
    QLabel[role="status-error"] {{
        background-color: {rgba(t.error, 0.14)};
        color: {t.error};
    }}
    QToolButton[role="collapsible-header"] {{
        background: transparent;
        border: none;
        color: {t.text_primary};
        padding: 6px 0;
        font-weight: 600;
        text-align: left;
    }}
    QToolButton[role="collapsible-header"]:hover {{
        color: {t.accent};
    }}
    QToolButton:focus, QPushButton:focus, QComboBox:focus,
    QLineEdit:focus, QListWidget:focus {{
        border-color: {t.accent};
    }}
    QWidget[role="accent-card"] {{
        background-color: {rgba(t.accent, 0.1)};
        border-radius: {t.radius_md};
    }}
    QLabel[role="chat-bubble-user"] {{
        background-color: {rgba(t.accent, 0.18)};
        border-top-left-radius: {t.radius_lg};
        border-top-right-radius: {t.radius_lg};
        border-bottom-left-radius: {t.radius_lg};
        border-bottom-right-radius: 2px;
        padding: 8px 12px;
        color: {t.text_primary};
    }}
    QLabel[role="chat-bubble-assistant"] {{
        background-color: {rgba(t.text_primary, 0.06)};
        border-top-left-radius: {t.radius_lg};
        border-top-right-radius: {t.radius_lg};
        border-bottom-left-radius: 2px;
        border-bottom-right-radius: {t.radius_lg};
        padding: 8px 12px;
        color: {t.text_primary};
    }}
    QLabel[role="chat-bubble-error"] {{
        background-color: {rgba(t.error, 0.12)};
        border-top-left-radius: {t.radius_lg};
        border-top-right-radius: {t.radius_lg};
        border-bottom-left-radius: 2px;
        border-bottom-right-radius: {t.radius_lg};
        padding: 8px 12px;
        color: {t.error};
    }}
    QPushButton[role="icon-button"] {{
        background-color: transparent;
        border: none;
        border-radius: {t.radius_sm};
        padding: 4px;
        color: {t.text_secondary};
    }}
    QPushButton[role="icon-button"]:hover {{
        background-color: {t.bg_pressed};
        color: {t.text_primary};
    }}
    QPushButton[role="icon-button"]:pressed {{
        background-color: {t.border};
    }}
    QPushButton[role="quick-chip"] {{
        background-color: transparent;
        border: none;
        border-radius: 16px;
        padding: 6px 12px;
        color: {t.text_secondary};
        font-size: {t.font_md};
        font-weight: 500;
    }}
    QPushButton[role="quick-chip"]:hover {{
        background-color: {t.bg_pressed};
        color: {t.text_primary};
    }}
    /* No font-weight here on purpose: Qt sizes a QPushButton from the
       font it resolves outside pseudo-states, so a bolder :checked font
       is painted wider than the widget the layout reserved for it and
       the label gets clipped at both ends ("Только расшифровка" ->
       "олько расшифровк" on the start screen's selected recipe chip).
       The filled pill plus the accent colour already carry the selected
       state, and the fill is a non-colour signal on its own. */
    QPushButton[role="quick-chip"]:checked {{
        background-color: {rgba(t.accent, 0.15)};
        color: {t.accent};
    }}
    QPushButton[role="quick-chip"]:checked:hover {{
        background-color: {rgba(t.accent, 0.24)};
        color: {t.accent_pressed};
    }}
    QWidget[role="drop-zone"] {{
        border: 2px dashed {t.border_input};
        border-radius: {t.radius_lg};
        background-color: {rgba(t.accent, 0.05)};
        min-height: 180px;
    }}
    QWidget[role="drop-zone"]:hover {{
        border-color: {t.accent};
        background-color: {rgba(t.accent, 0.1)};
    }}
    QWidget[role="drop-zone-active"] {{
        border: 2px dashed {t.accent};
        border-radius: {t.radius_lg};
        background-color: {rgba(t.accent, 0.15)};
        min-height: 180px;
    }}
    QPushButton[role="icon-button-danger"] {{
        background: transparent;
        border: none;
    }}
    QPushButton[role="icon-button-danger"]:hover {{
        background: {rgba(t.error, 0.1)};
        border-radius: 12px;
    }}
    QLabel[role="chip"] {{
        background-color: {t.bg_elevated};
        color: {t.text_secondary};
        border-radius: {t.radius_sm};
        padding: 2px 8px;
        font-size: {t.font_xs};
    }}
    QPushButton[role="accent-badge"] {{
        background-color: {rgba(t.accent, 0.2)};
        border: none;
        border-radius: 12px;
        padding: 4px 12px;
        color: {t.accent};
        font-size: {t.font_sm};
    }}
    QPushButton[role="accent-badge"]:hover {{
        background-color: {rgba(t.accent, 0.3)};
    }}
    QPushButton[role="success-badge"] {{
        background-color: {rgba(t.success, 0.2)};
        border: none;
        border-radius: 12px;
        padding: 4px 12px;
        color: {t.success};
        font-size: {t.font_sm};
    }}
    QPushButton[role="success-badge"]:hover {{
        background-color: {rgba(t.success, 0.3)};
    }}
    QPushButton[role="muted-badge"] {{
        background-color: {rgba(t.text_secondary, 0.2)};
        border: none;
        border-radius: 12px;
        padding: 4px 12px;
        color: {t.text_secondary};
        font-size: {t.font_sm};
    }}
    QPushButton[role="muted-badge"]:hover {{
        background-color: {rgba(t.text_secondary, 0.3)};
    }}
    QLabel[role="drop-overlay"] {{
        background-color: {rgba(t.accent, 0.18)};
        border: 2px dashed {t.accent};
        border-radius: {t.radius_lg};
        color: {t.accent};
        font-size: 20px;
        font-weight: bold;
    }}

    /* ── Library Card Widgets ── */
    QWidget[role="library-item-card"] {{
        background: transparent;
    }}
    QLabel[role="library-item-title"] {{
        font-weight: bold;
        font-size: {t.font_md};
        color: {t.text_primary};
        background: transparent;
    }}
    QLabel[role="library-item-meta"] {{
        font-size: {t.font_xs};
        color: {t.text_secondary};
        background: transparent;
    }}
    QLabel[role="badge-pill-transcript"] {{
        background-color: {rgba(t.accent, 0.15)};
        color: {t.accent};
        border-radius: 9px;
        padding: 2px 8px;
        font-size: {t.font_xs};
        font-weight: bold;
    }}
    QLabel[role="badge-pill-youtube"] {{
        background-color: {rgba(t.warning, 0.15)};
        color: {t.warning};
        border-radius: 9px;
        padding: 2px 8px;
        font-size: {t.font_xs};
        font-weight: bold;
    }}
    QLabel[role="badge-pill-article"] {{
        background-color: {rgba(t.success, 0.15)};
        color: {t.success};
        border-radius: 9px;
        padding: 2px 8px;
        font-size: {t.font_xs};
        font-weight: bold;
    }}
    QLabel[role="badge-pill-error"] {{
        background-color: {rgba(t.error, 0.15)};
        color: {t.error};
        border-radius: 9px;
        padding: 2px 8px;
        font-size: {t.font_xs};
        font-weight: bold;
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
