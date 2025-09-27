from __future__ import annotations

from .qt import Qt, QMainWindow, QTabWidget

from .tabs.convert_tab import ConvertTab
from .tabs.anonymization_tab import AnonymizationTab
from .tabs.settings_tab import SettingsTab
from .tabs.jobs_tab import JobsTab
from .tabs.lang_detect_tab import LanguageDetectTab
from .tabs.anon_batch_tab import AnonBatchTab  # new import


class MainWindow(QMainWindow):
    """Top-level window with tabbed navigation.

    Tabs:
        - Preprocess: Convert to Markdown (plan + run)
        - Language Detection: Detect primary language of a document
        - Anonymization: Detect / Pseudonymize / Deanonymize
        - Anon Batch: Folder anonymization (deterministic or pseudonymize)  # new
        - Jobs: History & logs (placeholder for now)
        - Settings: Global configuration and preflight checks
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Habithon GUI")
        self.resize(1100, 720)

        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        tabs.setTabPosition(QTabWidget.North)
        tabs.setElideMode(Qt.ElideRight)

        self.convert_tab = ConvertTab(self)
        self.lang_tab = LanguageDetectTab(self)
        self.anon_tab = AnonymizationTab(self)
        self.anon_batch_tab = AnonBatchTab(self)  # new
        self.jobs_tab = JobsTab(self)
        self.settings_tab = SettingsTab(self)

        tabs.addTab(self.convert_tab, "Preprocess")
        tabs.addTab(self.lang_tab, "Language Detection")
        tabs.addTab(self.anon_tab, "Anonymization")
        tabs.addTab(self.anon_batch_tab, "Anon Batch")  # new
        tabs.addTab(self.jobs_tab, "Jobs")
        tabs.addTab(self.settings_tab, "Settings")

        self.setCentralWidget(tabs)

    def cancel_all_jobs(self) -> None:
        for tab in [
            getattr(self, "convert_tab", None),
            getattr(self, "lang_tab", None),
            getattr(self, "anon_tab", None),
            getattr(self, "anon_batch_tab", None),
        ]:
            if tab is None:
                continue
            try:
                if hasattr(tab, "cancel_all_jobs"):
                    tab.cancel_all_jobs()  # type: ignore[attr-defined]
            except Exception:
                pass

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """Attempt to cancel and join all background jobs before closing.

        This prevents Qt from aborting with 'QThread: Destroyed while thread is still running'.
        """
        try:
            self.cancel_all_jobs()
        except Exception:
            pass
        try:
            super().closeEvent(event)
        except Exception:
            # In case the base implementation raises, still let the window close
            pass
