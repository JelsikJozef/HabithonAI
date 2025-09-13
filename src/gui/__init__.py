"""Habithon GUI (presentation layer)

This package hosts the desktop GUI built with PySide6 (Qt for Python).
Architecture mapping:
- presentation: gui.views (Qt widgets) and gui.app (entry point)
- app: gui.services (thin facade orchestrating calls to existing app-layer modules)
- adapters: imported from existing subpackages (preprocessing/adapters, anonymization/adapters) when wiring services
- domain: existing domain models and ports remain unchanged

The GUI aims to be offline-friendly and run entirely in-process.
"""

__all__ = ["__version__"]
__version__ = "0.1.0"
