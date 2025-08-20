from typing import List, Optional


def choose_language(requested: Optional[str], supported: List[str], fallback_lang: Optional[str]) -> Optional[str]:
    """Choose the language to use for analysis.

    Parameters
    - requested: Language requested by the caller (e.g., "en").
    - supported: Languages supported by the underlying analyzer/registry.
    - fallback_lang: Preferred fallback language when requested is not supported.

    Returns
    - Language code to use for analysis, preferring requested when supported,
      otherwise fallback_lang, else first supported or the requested value.
    """
    if not supported:
        # Analyzer might internally default to English
        return requested or fallback_lang
    if requested and requested in supported:
        return requested
    return fallback_lang if fallback_lang in supported else (supported[0] if supported else requested)
