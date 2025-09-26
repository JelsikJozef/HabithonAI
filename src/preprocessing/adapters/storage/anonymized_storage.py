# filepath: /Users/jozefjelsik/PycharmProjects/HabithonAI/src/preprocessing/adapters/storage/anonymized_storage.py
from __future__ import annotations

from pathlib import Path


class AnonymizedFileStorage:
    """Store anonymized Markdown files in a designated directory.

    Layout:
    - English inputs live under: <out_root>/en/.../file.md
    - Anonymized outputs go under: <out_root>/hashed_documents/.../file_anon.md

    Behavior:
    - Preserves the subdirectory structure relative to the English root when possible
    - Uses LF newlines and UTF-8 encoding
    """

    def __init__(
        self,
        out_root: str | Path,
        *,
        en_root_name: str = "en",
        hashed_root_name: str = "hashed_documents",
    ) -> None:
        self.out_root = Path(out_root).resolve()
        self.en_root = self.out_root / en_root_name
        self.hashed_root = self.out_root / hashed_root_name

    def compute_target_path(self, en_md_path: str | Path) -> Path:
        p = Path(en_md_path).resolve()
        try:
            rel = p.relative_to(self.en_root.resolve())
        except Exception:
            # Fallback: flatten into hashed_documents root
            rel = Path(p.name)
        target = (self.hashed_root / rel).with_suffix("")
        # Add _anon suffix and .md extension
        target = target.with_name(target.name + "_anon").with_suffix(".md")
        return target

    def resolve_en_path(self, anon_md_path: str | Path) -> Path:
        """Compute the original English path for a given anonymized file path.

        Mapping: <out>/hashed_documents/<rel>_anon.md -> <out>/en/<rel>.md
        """
        p = Path(anon_md_path).resolve()
        try:
            rel = p.relative_to(self.hashed_root.resolve())
        except Exception:
            # Best effort: strip _anon suffix in name and place under en root
            name = p.name
            if name.endswith("_anon.md"):
                base = name[:-9]
            else:
                base = p.stem
            return (self.en_root / base).with_suffix(".md")
        # Remove _anon suffix and set .md
        stem = rel.stem
        if stem.endswith("_anon"):
            stem = stem[: -len("_anon")]
        en_rel = rel.with_name(stem).with_suffix(".md")
        return (self.en_root / en_rel).resolve()

    def write(self, en_md_path: str | Path, anonymized_text: str) -> Path:
        dst = self.compute_target_path(en_md_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        text_lf = str(anonymized_text).replace("\r\n", "\n").replace("\r", "\n")
        dst.write_text(text_lf, encoding="utf-8", newline="\n")
        return dst

    def read(self, en_md_path: str | Path) -> str:
        src = self.compute_target_path(en_md_path)
        return src.read_text(encoding="utf-8")
