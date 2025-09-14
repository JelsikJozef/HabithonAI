from __future__ import annotations

from .qt import Qt, QMainWindow, QTabWidget

from .tabs.convert_tab import ConvertTab
from .tabs.anonymization_tab import AnonymizationTab
from .tabs.settings_tab import SettingsTab
from .tabs.jobs_tab import JobsTab
from .tabs.lang_detect_tab import LanguageDetectTab


class MainWindow(QMainWindow):
    """Top-level window with tabbed navigation.

    Tabs:
        - Preprocess: Convert to Markdown (plan + run)
        - Language Detection: Detect primary language of a document
        - Anonymization: Detect / Pseudonymize / Deanonymize
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
        self.jobs_tab = JobsTab(self)
        self.settings_tab = SettingsTab(self)

        tabs.addTab(self.convert_tab, "Preprocess")
        tabs.addTab(self.lang_tab, "Language Detection")
        tabs.addTab(self.anon_tab, "Anonymization")
        tabs.addTab(self.jobs_tab, "Jobs")
        tabs.addTab(self.settings_tab, "Settings")

        self.setCentralWidget(tabs)
