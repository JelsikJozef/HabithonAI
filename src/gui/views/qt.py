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
    from PySide6.QtCore import QObject as _QObject  # type: ignore
    from PySide6.QtCore import Signal as _Signal  # type: ignore
    from PySide6.QtCore import QThread as _QThread  # type: ignore
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
        QToolButton as _QToolButton,
        QFileDialog as _QFileDialog,
        QCheckBox as _QCheckBox,
        QTextEdit as _QTextEdit,
        QSpinBox as _QSpinBox,
        QComboBox as _QComboBox,
        QLabel as _QLabel,
        QStyle as _QStyle,
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
    QToolButton = _QToolButton
    QFileDialog = _QFileDialog
    QCheckBox = _QCheckBox
    QTextEdit = _QTextEdit
    QSpinBox = _QSpinBox
    QComboBox = _QComboBox
    QLabel = _QLabel
    QStyle = _QStyle
    QObject = _QObject
    Signal = _Signal
    QThread = _QThread

except Exception:  # pragma: no cover - stub fallback
    QT_AVAILABLE = False

    class _QtStub:
        ElideRight = 0

    class _Signal:
        """Minimal signal stub with connect/emit no-ops.

        In non-GUI environments, handlers are stored and called synchronously on emit.
        """

        def __init__(self, *args, **kwargs) -> None:
            self._subs: list = []

        def connect(self, fn, *args, **kwargs):
            self._subs.append(fn)

        def emit(self, *args, **kwargs):
            for fn in list(self._subs):
                try:
                    fn(*args, **kwargs)
                except Exception:
                    pass

    class _Widget:
        def __init__(self, *args, **kwargs):
            pass

        def setToolTip(self, *args, **kwargs):
            pass

        def setEnabled(self, *args, **kwargs):
            pass

        def setStyleSheet(self, *args, **kwargs):
            pass

        def setObjectName(self, *args, **kwargs):
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

        def setContentsMargins(self, *args, **kwargs):
            pass

        def setSpacing(self, *args, **kwargs):
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

        def setReadOnly(self, *args, **kwargs):
            pass

    class QPushButton(_Widget):
        def __init__(self, *args, **kwargs):
            self.clicked = _Signal()

        def setToolTip(self, *args, **kwargs):
            pass

    class QToolButton(_Widget):
        def __init__(self, *args, **kwargs):
            self.clicked = _Signal()

        def setText(self, *args, **kwargs):
            pass

    class QFileDialog:
        @staticmethod
        def getExistingDirectory(*args, **kwargs) -> str:
            return ""

        @staticmethod
        def getOpenFileName(*args, **kwargs):
            # Return tuple (path, filter)
            return ("", "")

        @staticmethod
        def getSaveFileName(*args, **kwargs):
            # Return tuple (path, filter)
            return ("", "")

    class QCheckBox(_Widget):
        def __init__(self, *args, **kwargs):
            self.toggled = _Signal()

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

        def setMinimumHeight(self, *args, **kwargs):
            pass

    class QSpinBox(_Widget):
        def setRange(self, *args, **kwargs):
            pass

        def setValue(self, *args, **kwargs):
            pass

        def setSuffix(self, *args, **kwargs):
            pass

        def value(self) -> int:
            return 1

        def setToolTip(self, *args, **kwargs):
            pass

        def setEnabled(self, *args, **kwargs):
            pass

    class QComboBox(_Widget):
        def addItems(self, *args, **kwargs):
            pass

        def setEditable(self, *args, **kwargs):
            pass

        def currentText(self) -> str:
            return ""

        def setCurrentText(self, *args, **kwargs):
            pass

        def count(self) -> int:
            return 0

        def itemText(self, i: int) -> str:  # noqa: ARG002
            return ""

        def minimumWidth(self) -> int:
            return 0

        def setMinimumWidth(self, *args, **kwargs):
            pass

        def view(self):
            return self

        def setSizeAdjustPolicy(self, *args, **kwargs):
            pass

    class QLabel(_Widget):
        def __init__(self, *args, **kwargs):
            pass

        def setText(self, *args, **kwargs):
            pass

        def setStyleSheet(self, *args, **kwargs):
            pass

    class QStyle:
        # Minimal stub to satisfy imports
        pass

    class QObject:  # minimal stub
        def moveToThread(self, *args, **kwargs):
            pass

    class QThread:  # minimal stub
        def __init__(self, *args, **kwargs):
            # Provide a started Signal so callers can connect slots
            self.started = _Signal()
            self._t = None

        def start(self):
            # Launch a real Python thread that emits started; slots will run in that thread
            import threading

            def _runner():
                try:
                    self.started.emit()
                except Exception:
                    pass

            self._t = threading.Thread(target=_runner, name="QtStubThread", daemon=True)
            self._t.start()

        def quit(self):
            # No-op; cooperative cancellation should be handled by worker
            pass

        def wait(self):
            try:
                if self._t and self._t.is_alive():
                    self._t.join(timeout=5.0)
            except Exception:
                pass

    # Expose Signal class for type compat
    Signal = _Signal
