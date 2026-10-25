#!/usr/bin/env python
"""免安装入口：把 src/ 放进 sys.path 后调用 CLI。

    python run.py sync
    python run.py build
    python run.py check
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from endfield.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
