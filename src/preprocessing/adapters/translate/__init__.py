# Re-export adapters for convenient imports
from .ct2_nllb import NllbCTranslate2
from .marian_opus import MarianOpus

__all__ = [
    "NllbCTranslate2",
    "MarianOpus",
]
