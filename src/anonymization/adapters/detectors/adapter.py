# Compatibility shim: keep the original import path but delegate to the modular PresidioDetector
from anonymization.adapters.detectors.presidio.detector import PresidioDetector  # noqa: F401
