from __future__ import annotations

"""Reusable small UI helper widgets (info icons, headers) for consistent styling.

Relies on styles set in theme.py (objectName selectors):
  - QLabel#infoIcon
  - QLabel#sectionHeader
"""
from .qt import QLabel, QWidget, QHBoxLayout

try:  # optional imports for type hints (ignored if stubs)
    from .qt import QComboBox  # type: ignore
except Exception:  # pragma: no cover
    QComboBox = object  # type: ignore


def create_info_icon(tooltip: str) -> QLabel:
    lbl = QLabel("ⓘ")
    lbl.setObjectName("infoIcon")
    lbl.setToolTip(tooltip)
    return lbl


def section_header(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("sectionHeader")
    return lbl


def create_field_label(text: str, tooltip: str) -> QWidget:
    """Return a container with a plain text label + info icon sharing the same tooltip."""
    container = QWidget()
    lay = QHBoxLayout(container)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    lbl = QLabel(text)
    icon = create_info_icon(tooltip)
    lay.addWidget(lbl)
    lay.addWidget(icon)
    lay.addStretch(1)
    return container


def auto_expand_combo(combo: "QComboBox", extra: int = 36) -> None:
    """Ensure a combo box is wide enough for its longest item.

    Safe no-op under stub Qt or if measurement fails.
    """
    try:  # pragma: no cover - GUI environment dependent
        fm = combo.fontMetrics()
        max_w = 0
        for i in range(combo.count()):
            txt = combo.itemText(i)
            max_w = max(max_w, fm.horizontalAdvance(txt))
        target = max_w + extra
        if target > combo.minimumWidth():
            combo.setMinimumWidth(target)
        # Adjust popup width
        view = combo.view()
        if hasattr(view, "setMinimumWidth"):
            view.setMinimumWidth(target + 24)
        if hasattr(combo, "setSizeAdjustPolicy"):
            from PySide6.QtWidgets import QComboBox as _QC  # type: ignore

            combo.setSizeAdjustPolicy(_QC.AdjustToContents)
    except Exception:
        return


def wrap_with_help(widget: QWidget, tooltip: str) -> QWidget:
    """Return a container with the widget + trailing info icon.

    Useful for standalone checkboxes or buttons where a label wrapper
    is not appropriate.
    """
    container = QWidget()
    lay = QHBoxLayout(container)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    lay.addWidget(widget)
    lay.addWidget(create_info_icon(tooltip))
    lay.addStretch(1)
    return container
