from __future__ import annotations

"""Centralized Slovak prompt definitions for LLM enrichment.

This module contains reusable builders for summary/keywords prompts (prompt-based)
and chat messages for JSON-based structured output. Keep content in Slovak.
"""


class PromptTexts:
    def __init__(self):
        # For JSON-based backend
        self.json_system = (
            "Si užitočný asistent. Z textu vytvor stručné, vecné zhrnutie v slovenčine a vyber zoznam relevantných kľúčových slov. "
            "Ak sa v texte nachádzajú špeciálne PII tokeny vo formáte {{PII:...}}, zachovaj ich presne bez zmien."
        )
        # Template hint used by user content for JSON backend
        self.json_user_template = (
            "VÝSTUP v JSON (bez ďalšieho textu): {json_schema}.\n"
            "Požiadavky: zhrnutie 1–3 vety v slovenčine; tags je zoznam 1–{top_k} krátkych slovenských výrazov; "
            "nezavádzaj ďalší text.\n\nText:\n{text}"
        )
        # For prompt-based backend
        self.summarize_prefix = (
            "Si stručný asistent. Zhrň nasledujúci text do 1–3 viet v slovenskom jazyku. "
            "Nevkladaj úvodné ani záverečné poznámky. VÝSTUP: iba samotné zhrnutie.\n\n"
            "Dôležité: Ak text obsahuje špeciálne PII tokeny vo formáte {{PII:...}}, zachovaj ich presne tak, ako sú (bez zmien).\n\n"
            "Text:\n"
        )
        self.keywords_prefix = (
            "Extrahuj stručné kľúčové slová alebo krátke frázy zo vstupu v slovenskom jazyku (max {top_k}). "
            "Vráť jednoduchý zoznam oddelený čiarkami, bez číslovania a bez dodatočného textu.\n\n"
            "Dôležité: Ak text obsahuje špeciálne PII tokeny vo formáte {{PII:...}}, zachovaj ich presne tak, ako sú (bez zmien).\n\n"
            "Text:\n"
        )


DEFAULT_PROMPTS = PromptTexts()


def build_summarize_prompt(text: str, prompts: PromptTexts = DEFAULT_PROMPTS) -> str:
    return prompts.summarize_prefix + text


def build_keywords_prompt(text: str, top_k: int, prompts: PromptTexts = DEFAULT_PROMPTS) -> str:
    return prompts.keywords_prefix.format(top_k=top_k) + text


def build_json_messages(text: str, top_k: int, prompts: PromptTexts = DEFAULT_PROMPTS) -> list[dict[str, str]]:
    schema = '{"summary": "...", "tags": ["...", "..."]}'
    user = prompts.json_user_template.format(json_schema=schema, top_k=max(1, top_k), text=text)
    return [
        {"role": "system", "content": prompts.json_system},
        {"role": "user", "content": user},
    ]
