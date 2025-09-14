from __future__ import annotations

"""Qt compatibility shim for the GUI.

- Tries to import PySide6 symbols used by the app.
- Falls back to lightweight no-op stubs so static analysis/tests can import modules
  even when PySide6 isn't installed.

This avoids hard import failures in non-GUI environments while keeping runtime
behavior correct when PySide6 is available.
"""

try:  # pragma: no cover - import-time capability check
    from PySide6.QtCore import Qt as _Qt  # type: ignore
    from PySide6.QtWidgets import (  # type: ignore
        QApplication as _QApplication,
        QMainWindow as _QMainWindow,
        QTabWidget as _QTabWidget,
        QWidget as _QWidget,
        QVBoxLayout as _QVBoxLayout,
        QFormLayout as _QFormLayout,
        QHBoxLayout as _QHBoxLayout,
        QLineEdit as _QLineEdit,
        QPushButton as _QPushButton,
        QFileDialog as _QFileDialog,
        QCheckBox as _QCheckBox,
        QTextEdit as _QTextEdit,
        QSpinBox as _QSpinBox,
        QComboBox as _QComboBox,
        QLabel as _QLabel,
    )

    QT_AVAILABLE = True
    Qt = _Qt
    QApplication = _QApplication
    QMainWindow = _QMainWindow
    QTabWidget = _QTabWidget
    QWidget = _QWidget
    QVBoxLayout = _QVBoxLayout
    QFormLayout = _QFormLayout
    QHBoxLayout = _QHBoxLayout
    QLineEdit = _QLineEdit
    QPushButton = _QPushButton
    QFileDialog = _QFileDialog
    QCheckBox = _QCheckBox
    QTextEdit = _QTextEdit
    QSpinBox = _QSpinBox
    QComboBox = _QComboBox
    QLabel = _QLabel

except Exception:  # pragma: no cover - stub fallback
    QT_AVAILABLE = False

    class _QtStub:
        ElideRight = 0

    class _Signal:  # minimal signal stub
        def connect(self, *args, **kwargs):
            pass

    class _Widget:
        def __init__(self, *args, **kwargs):
            pass

        def setToolTip(self, *args, **kwargs):
            pass

    class _Layout:
        def __init__(self, *args, **kwargs):
            pass

        def addWidget(self, *args, **kwargs):
            pass

        def addLayout(self, *args, **kwargs):
            pass

        def addRow(self, *args, **kwargs):
            pass

        def addStretch(self, *args, **kwargs):
            pass

    class Qt(_QtStub):
        pass

    class QApplication:
        def __init__(self, *args, **kwargs):
            pass

        def setApplicationName(self, *args, **kwargs):
            pass

        def setOrganizationName(self, *args, **kwargs):
            pass

        def exec(self) -> int:  # type: ignore[override]
            return 0

    class QMainWindow(_Widget):
        def setWindowTitle(self, *args, **kwargs):
            pass

        def resize(self, *args, **kwargs):
            pass

        def setCentralWidget(self, *args, **kwargs):
            pass

        def show(self):
            pass

    class QTabWidget(_Widget):
        North = 0

        def setDocumentMode(self, *args, **kwargs):
            pass

        def setTabPosition(self, *args, **kwargs):
            pass

        def setElideMode(self, *args, **kwargs):
            pass

        def addTab(self, *args, **kwargs):
            pass

    class QWidget(_Widget):
        pass

    class QVBoxLayout(_Layout):
        def __init__(self, *args, **kwargs):
            pass

    class QFormLayout(_Layout):
        pass

    class QHBoxLayout(_Layout):
        pass

    class QLineEdit(_Widget):
        def setText(self, *args, **kwargs):
            pass

        def text(self) -> str:
            return ""

        def setPlaceholderText(self, *args, **kwargs):
            pass

    class QPushButton(_Widget):
        def __init__(self, *args, **kwargs):
            self.clicked = _Signal()

    class QFileDialog:
        @staticmethod
        def getExistingDirectory(*args, **kwargs) -> str:
            return ""

        @staticmethod
        def getOpenFileName(*args, **kwargs):
            # Return tuple (path, filter)
            return ("", "")

    class QCheckBox(_Widget):
        def setChecked(self, *args, **kwargs):
            pass

        def isChecked(self) -> bool:
            return False

    class QTextEdit(_Widget):
        def setReadOnly(self, *args, **kwargs):
            pass

        def setPlainText(self, *args, **kwargs):
            pass

        def toPlainText(self) -> str:
            return ""

        def setPlaceholderText(self, *args, **kwargs):
            pass

    class QSpinBox(_Widget):
        def setRange(self, *args, **kwargs):
            pass

        def setValue(self, *args, **kwargs):
            pass

        def value(self) -> int:
            return 1

    class QComboBox(_Widget):
        def addItems(self, *args, **kwargs):
            pass

        def setEditable(self, *args, **kwargs):
            pass

        def currentText(self) -> str:
            return ""

    class QLabel(_Widget):
        def __init__(self, *args, **kwargs):
            pass
