#!/usr/bin/env python3
"""Start the APRIL backend from the project venv."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / ".venv" / "bin" / "python"


def main() -> int:
    if not BACKEND.exists():
        print("Backend venv was not found at .venv/bin/python.", file=sys.stderr)
        return 1

    env = os.environ.copy()
    env.setdefault("APRIL_API_PORT", "8000")
    backend = subprocess.Popen(
        [str(BACKEND), "-m", "april.cli.main", "--api"],
        cwd=str(ROOT),
        env=env,
    )

    print("APRIL backend: http://127.0.0.1:8000")
    print("Frontend: removed; it will return as the Electron app.")
    print("Press Ctrl+C to stop.")

    try:
        backend.wait()
    except KeyboardInterrupt:
        print("\nStopping the backend...")
        backend.terminate()
        try:
            backend.wait(timeout=5)
        except subprocess.TimeoutExpired:
            backend.kill()
            backend.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
