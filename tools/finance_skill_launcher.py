#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    root = Path(os.environ.get("HERMES_SKILL_DIR", Path.home() / ".hermes" / "skills" / "finance"))
    python = root / ".venv" / "bin" / "python"
    executable = str(python if python.is_file() else Path(sys.executable))
    os.execv(executable, [executable, str(root / "scripts" / "finance.py"), *sys.argv[1:]])


if __name__ == "__main__":
    main()
