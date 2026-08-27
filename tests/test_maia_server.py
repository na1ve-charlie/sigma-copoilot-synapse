from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maia import server
from maia.recognition import config as recognition_config


def test_environment_file_loads_values_without_overriding_process_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "app.env"
    path.write_text(
        "# production settings\nSIGMA_BASE_URL=http://sigma.local\n"
        'SIGMA_TOKEN="file-token"\nMAIA_PORT=9010\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("SIGMA_TOKEN", "process-token")
    monkeypatch.delenv("SIGMA_BASE_URL", raising=False)
    monkeypatch.delenv("MAIA_PORT", raising=False)

    server._load_environment(path)

    assert os.environ["SIGMA_BASE_URL"] == "http://sigma.local"
    assert os.environ["SIGMA_TOKEN"] == "process-token"
    assert server._environment_port() == 9010


def test_environment_file_rejects_invalid_line(tmp_path: Path) -> None:
    path = tmp_path / "app.env"
    path.write_text("NOT VALID\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid environment entry"):
        server._load_environment(path)


def test_demo_page_is_served_from_packaged_asset(tmp_path: Path) -> None:
    page_path = tmp_path / "chat.html"
    page_path.write_text("<h1>Demo ready</h1>", encoding="utf-8")
    app = FastAPI()

    server._attach_demo_page(app, page_path)

    client = TestClient(app)
    assert client.get("/").text == "<h1>Demo ready</h1>"
    assert client.get("/chat").status_code == 200


def test_demo_page_requires_packaged_asset(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="demo page not found"):
        server._attach_demo_page(FastAPI(), tmp_path / "missing.html")


def test_packaged_config_path_is_next_to_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "SigmaMaia.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))

    assert recognition_config._application_root() == tmp_path


@pytest.mark.parametrize("value", ["0", "65536"])
def test_port_rejects_out_of_range_values(value: str) -> None:
    with pytest.raises(ValueError, match="port must be between"):
        server._port(value)
