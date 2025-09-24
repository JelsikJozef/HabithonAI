from __future__ import annotations

"""GUI theming utilities.

Provides a centrally defined stylesheet that adapts to light/dark mode and
helper to apply it early in application startup.

Usage:
    from gui.views.theme import apply_theme
    apply_theme(app)  # auto-detect

Object names / classes used across widgets:
    QLabel#infoIcon         (small circular info glyph)
    QLabel#sectionHeader    (section headers inside forms)

We intentionally keep palette neutral and rely on stylesheet for high-contrast
Tooltip readability independent of OS dark mode.
"""
from typing import Literal
from .qt import QApplication  # no QPalette stub guarantee in fallback

ThemeMode = Literal["light", "dark"]


def _detect_dark(app: QApplication) -> bool:
    """Best-effort dark mode detection.

    Falls back to False (light) if palette introspection is unavailable.
    """
    try:  # pragma: no cover - environment dependent
        pal = getattr(app, "palette", lambda: None)()
        if pal is None:
            return False
        # attempt to access color roles dynamically (avoid importing QPalette)
        win_role = getattr(pal, "Window", None)
        color = pal.color(win_role) if win_role is not None else pal.color(pal.Window)  # type: ignore
        r, g, b = color.red(), color.green(), color.blue()  # type: ignore
        brightness = r * 0.299 + g * 0.587 + b * 0.114
        return brightness < 128
    except Exception:
        return False


def build_stylesheet(mode: ThemeMode) -> str:
    if mode == "dark":
        bg_panel = "#222222"
        bg_alt = "#2b2b2b"
        txt_primary = "#e8e8e8"
        txt_muted = "#b0b0b0"
        accent = "#4ea3ff"
        border = "#444"
        tooltip_bg = "#3a3a3a"
        tooltip_border = "#555"
    else:
        bg_panel = "#ffffff"
        bg_alt = "#f5f7fa"
        txt_primary = "#222222"
        txt_muted = "#555555"
        accent = "#1e6bb8"
        border = "#d0d7de"
        tooltip_bg = "#fffffb"
        tooltip_border = "#b5b5b5"

    return f"""
/* Base panels */
QWidget {{ background: {bg_panel}; color: {txt_primary}; }}
QTextEdit, QLineEdit {{ background: {bg_alt}; color: {txt_primary}; border: 1px solid {border}; border-radius:4px; }}
QTextEdit:disabled, QLineEdit:disabled {{ color: {txt_muted}; }}
QComboBox, QSpinBox {{ background: {bg_alt}; color: {txt_primary}; border: 1px solid {border}; border-radius:4px; padding:2px; }}
QPushButton {{ background: {accent}; color: #fff; border: 1px solid {accent}; border-radius:4px; padding:4px 10px; }}
QPushButton:disabled {{ background: {border}; color: {txt_muted}; border-color: {border}; }}
QCheckBox {{ color: {txt_primary}; }}
QLabel#sectionHeader {{ font-weight:600; color: {accent}; padding:2px 0; }}
QLabel#infoIcon {{ color: {accent}; font-weight:bold; padding-left:4px; }}
QToolTip {{ background: {tooltip_bg}; color: {txt_primary}; border: 1px solid {tooltip_border}; padding:6px; border-radius:4px; font-size:11px; }}
QTextEdit, QLineEdit {{ selection-background-color: {accent}; selection-color: #fff; }}
"""


def apply_theme(app: QApplication, *, force: ThemeMode | None = None) -> ThemeMode:
    dark = _detect_dark(app) if force is None else (force == "dark")
    mode: ThemeMode = "dark" if dark else "light"
    # Guard missing setStyleSheet (stub Qt environment)
    if hasattr(app, "setStyleSheet"):
        try:
            app.setStyleSheet(build_stylesheet(mode))
        except Exception:  # pragma: no cover - non-fatal
            pass
    return mode
