"""Demo chat launcher — standalone, zero impact on src/maia/.

Usage:
    cd demo-chat
    python run.py              # default port 8000
    python run.py --port 9090  # custom port

Delete `demo-chat/` when the demo is done — nothing else is touched.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse

from maia.api.main import create_app

HERE = Path(__file__).resolve().parent
CHAT_HTML = HERE / "chat.html"


def _build_app() -> FastAPI:
    if not CHAT_HTML.is_file():
        sys.exit(f"chat.html not found at {CHAT_HTML} — place it next to run.py")

    app = create_app()

    @app.get("/chat")
    async def chat_page() -> FileResponse:
        return FileResponse(str(CHAT_HTML), media_type="text/html; charset=utf-8")

    @app.get("/")
    async def root() -> FileResponse:
        return FileResponse(str(CHAT_HTML), media_type="text/html; charset=utf-8")

    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Maia Demo Chat")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    app = _build_app()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
