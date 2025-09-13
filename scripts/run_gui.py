from __future__ import annotations

from pathlib import Path
import sys

# Ensure the repository 'src' directory is on sys.path so `import gui` works
_repo_root = Path(__file__).resolve().parent.parent
_src_dir = str(_repo_root / "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from gui.app import main


if __name__ == "__main__":
    raise SystemExit(main())
