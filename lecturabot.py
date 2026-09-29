"""Run LecturaBot directly from this checkout: python lecturabot.py."""

from __future__ import annotations

from pathlib import Path
import sys


def main() -> None:
    # Prefer this checkout's package without requiring an editable install.
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
    from lecturabot.__main__ import main as run_bot

    run_bot()


if __name__ == "__main__":
    main()
