# GUI Style Guide

This guide defines **visual + interaction conventions** for the Habithon GUI.
All new UI code must follow these rules to keep dark/light themes accessible and
eliminate duplication.

## 1. Theming
- Global palette and widget chrome are applied through `theme.apply_theme()`.
- Do **not** call `setStyleSheet()` directly on individual widgets for colors, fonts,
  borders, etc.
- Acceptable exceptions:
  - Temporary spike / debug code (must be removed before merge).
  - Global stylesheet application inside `theme.py`.
  - Inline style usage inside the Qt stub layer (`qt.py`) for test compatibility.
- Environment override: `HABITHON_GUI_THEME=light|dark`; otherwise auto-detects using
  background brightness.

## 2. Info / Help affordances
- Use `ui_helpers.create_field_label(text, tooltip)` to display a label with an info icon.
- Use `ui_helpers.create_info_icon(tooltip)` for standalone contextual hints.
- Never embed raw '?' buttons with bespoke styling; they are visually inconsistent
  and inaccessible in dark mode.

## 3. Section headers
- Use `ui_helpers.section_header("Title")` instead of inline font-weight / color styling.
- Headers automatically adapt to active theme and maintain contrast.

## 4. Batch / Multi-file operations
- Output summary sections should follow pattern:
  ```
  <TITLE>:
    key=value key=value ...
  ```
- Per-file status lists are capped (e.g., first 15) to avoid UI freezes.

## 5. Tooltips
- Must be concise; first sentence describes purpose; subsequent sentences
  optionally describe rationale or side-effects.
- Avoid raw HTML unless semantically necessary (e.g., `<code>` tags for tokens).

## 6. Accessibility / Contrast
- No pastel-on-pastel or low-contrast gray text.
- Rely on palette variables: primary text, muted text, accent, panel backgrounds.
- Avoid embedding raw hex colors outside `theme.py`.

## 7. File / Context IDs
- Use `shared.hashing.document_fingerprint()` when deriving per-file context IDs.
- Prefix deterministic context IDs with `ctx_` for consistency.

## 8. Anonymization Modes
- Deterministic: stable HMAC tokens `h:<kid>:<hex>`; tenant ID scopes hashing.
- Pseudonymize: ephemeral tokens `{{PII:TYPE:i:xxxx}}` (no cross-document correlation).
- Always surface a hint clarifying the selected mode in summaries.

## 9. Prohibited Patterns (static check enforced)
The test `tests/unit/test_gui_style.py` fails if any *active* (non-comment) line in
`src/gui/views/**` (excluding `theme.py` & `qt.py`) contains `setStyleSheet(`.

## 10. Adding New Tabs
1. Create a widget in `gui/views/tabs/`.
2. Use helpers for headers and labels; no inline styling.
3. Import and add it to `MainWindow` (avoid renaming existing tabs unexpectedly).
4. If the tab performs long tasks, plan eventual background worker integration.

## 11. Logging / Diagnostics
- Place GUI-generated JSON diagnostics under `outputs/logs/` with a timestamp prefix.
- Do not print directly to stdout from view logic (except temporary debug).

## 12. Future Enhancements (reserved guidelines)
- Hotkey conventions (e.g., Cmd/Ctrl+Shift+T for theme toggle) — pending.
- Snackbar / transient notification component — pending.

---
**Summary:** Centralize presentation concerns via `theme.py` + `ui_helpers.py`. Keep
widget code declarative, readable, and low-risk for dark mode regressions.
