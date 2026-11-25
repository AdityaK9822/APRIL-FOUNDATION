"""APRIL's one front door: TUI by default, ``--api`` for the FastAPI server."""

from __future__ import annotations

import argparse
import os


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="april", description="APRIL — an assistant with a body."
    )
    parser.add_argument(
        "--api", action="store_true", help="serve the FastAPI front door"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port", type=int, default=int(os.getenv("APRIL_API_PORT", "8000"))
    )
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args(argv)
    os.environ["APRIL_LOG_LEVEL"] = args.log_level

    if args.api:
        import uvicorn

        from ..api.app import create_app

        uvicorn.run(create_app(), host=args.host, port=args.port)
        return 0

    from .tui import APRILApp

    APRILApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
