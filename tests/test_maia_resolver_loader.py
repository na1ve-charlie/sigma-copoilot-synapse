from __future__ import annotations

import asyncio

import pytest

from maia.recognition.resolver_loader import load_cli_resolver


def run(coro):
    return asyncio.run(coro)


def test_load_cli_resolver_supports_values_wrapper(tmp_path) -> None:
    path = tmp_path / "resolver-values.yaml"
    path.write_text(
        "\n".join(
            [
                "values:",
                "  sensor: [Vib1, Vib2]",
                "  indicator: [RMS, Spectrum]",
            ]
        ),
        encoding="utf-8",
    )

    resolver = load_cli_resolver(path)

    assert run(resolver.resolve("sensor")) == ["Vib1", "Vib2"]
    assert run(resolver.resolve("indicator")) == ["RMS", "Spectrum"]


def test_load_cli_resolver_supports_explicit_themis_enum_config(tmp_path) -> None:
    path = tmp_path / "resolver-values.yaml"
    path.write_text(
        "\n".join(
            [
                "enum:",
                "  case_sensitive: false",
                "  values:",
                "    sensor: [Vib1]",
            ]
        ),
        encoding="utf-8",
    )

    resolver = load_cli_resolver(path)

    assert run(resolver.resolve("SENSOR")) == ["Vib1"]


def test_load_cli_resolver_rejects_empty_config(tmp_path) -> None:
    path = tmp_path / "resolver-values.yaml"
    path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="must define enum values or a Themis resolver config"):
        load_cli_resolver(path)
