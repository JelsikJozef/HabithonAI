"""DEPRECATED MODULE: convert_tab_fixed

This file is retained only as a historical reference. The active implementation
is `convert_tab.py` which now uses centralized theming (theme.py) and UI helpers
(ui_helpers.py). All new changes must target `convert_tab.py`.

Rationale for deprecation:
- Duplicated logic & styling (inline setStyleSheet calls) caused divergence.
- New unified helpers provide consistent dark/light appearance and tooltips.

If you need experimental variations, create a new module that composes the
shared helpers instead of copying the entire tab.
"""

# Intentionally left without functional code.
