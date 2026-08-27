"""Production launcher for the packaged Windows API service."""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LOG_LEVELS = {"critical", "error", "warning", "info", "debug", "trace"}


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        _load_environment(args.config)
        _set_packaged_model_cache()
        if args.check_config or args.check_model:
            _check_config()
        if args.check_model:
            return _check_model()
        if args.check_config:
            return 0
        host = args.host or os.getenv("MAIA_HOST", "127.0.0.1")
        port = args.port or _environment_port()
        log_level = (args.log_level or os.getenv("MAIA_LOG_LEVEL", "info")).lower()
        if log_level not in _LOG_LEVELS:
            raise ValueError(f"MAIA_LOG_LEVEL must be one of: {', '.join(sorted(_LOG_LEVELS))}")
    except (OSError, ValueError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    import uvicorn

    from maia.api.main import app

    try:
        if _demo_enabled():
            _attach_demo_page(app)
    except ValueError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=log_level,
        loop="asyncio",
        http="h11",
        workers=1,
        access_log=True,
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sigma Maia production API service")
    parser.add_argument("--config", type=Path, default=_default_env_path())
    parser.add_argument("--host")
    parser.add_argument("--port", type=_port)
    parser.add_argument("--log-level")
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument("--check-model", action="store_true")
    return parser


def _default_env_path() -> Path:
    root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()
    return root / "config" / "app.env"


def _load_environment(path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"environment file not found: {path}")
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not _ENV_KEY.fullmatch(key):
            raise ValueError(f"invalid environment entry at {path}:{line_number}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _set_packaged_model_cache() -> None:
    model_home = _default_env_path().parents[1] / "models" / "huggingface"
    if model_home.is_dir():
        os.environ.setdefault("HF_HOME", str(model_home))
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


def _environment_port() -> int:
    return _port(os.getenv("MAIA_PORT", "8000"))


def _demo_enabled() -> bool:
    return os.getenv("MAIA_ENABLE_DEMO", "0").strip().lower() in {"1", "true", "yes"}


def _attach_demo_page(app: Any, page_path: Path | None = None) -> None:
    from fastapi.responses import FileResponse

    page_path = page_path or _demo_page_path()
    if not page_path.is_file():
        raise ValueError(f"demo page not found: {page_path}")

    async def demo_page() -> FileResponse:
        return FileResponse(str(page_path), media_type="text/html; charset=utf-8")

    app.add_api_route("/", demo_page, methods=["GET"], include_in_schema=False)
    app.add_api_route("/chat", demo_page, methods=["GET"], include_in_schema=False)


def _demo_page_path() -> Path:
    root = _default_env_path().parents[1]
    packaged_path = root / "demo" / "chat.html"
    if packaged_path.is_file():
        return packaged_path
    return root / "demo-chat" / "chat.html"


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    return port


def _check_config() -> int:
    from maia.recognition.config import load_recognition_config

    config = load_recognition_config()
    paths = [config.config_path, config.intents_path, config.report_contract_path]
    if config.tree_prompt_path is not None:
        paths.append(config.tree_prompt_path)
    missing = [path for path in paths if not path.exists()]
    if missing:
        raise ValueError("missing recognition files: " + ", ".join(map(str, missing)))
    print(f"configuration OK: {config.config_path}")
    return 0


def _check_model() -> int:
    from sentence_transformers import SentenceTransformer
    from themis import RecognitionConfig

    model_name = RecognitionConfig().embedding_model
    model = SentenceTransformer(model_name, local_files_only=True)
    embedding = model.encode(["Sigma Maia embedding self-check"])
    if getattr(embedding, "shape", ())[:1] != (1,):
        raise ValueError("embedding model returned an unexpected result")
    print(f"embedding model OK: {model_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
