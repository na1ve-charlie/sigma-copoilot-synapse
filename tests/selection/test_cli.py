"""Tests for Selection CLI (Task 14)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "synapse.selection.cli", *args],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[2]),
    )


# ======================================================================
# parse-time-range
# ======================================================================


class TestParseTimeRange:
    def test_relative_last_7_days(self) -> None:
        r = _run(
            "parse-time-range",
            "--value", "relative:last_7_days",
            "--now", "2026-06-10T15:30:00+08:00",
        )
        assert r.returncode == 0
        out = json.loads(r.stdout)
        assert out["start"] == "2026-06-04T00:00:00+08:00"
        assert out["end"] == "2026-06-10T23:59:59.999999+08:00"

    def test_relative_today(self) -> None:
        r = _run(
            "parse-time-range",
            "--value", "relative:today",
            "--now", "2026-06-10T15:30:00+08:00",
        )
        assert r.returncode == 0
        out = json.loads(r.stdout)
        assert out["start"] == "2026-06-10T00:00:00+08:00"
        assert out["end"] == "2026-06-10T23:59:59.999999+08:00"

    def test_absolute_interval(self) -> None:
        r = _run(
            "parse-time-range",
            "--value", "2026-06-01T00:00:00+08:00/2026-06-10T23:59:59+08:00",
            "--now", "2026-06-10T15:30:00+08:00",
        )
        assert r.returncode == 0
        out = json.loads(r.stdout)
        assert out["start"] == "2026-06-01T00:00:00+08:00"
        assert out["end"] == "2026-06-10T23:59:59+08:00"

    def test_invalid_value(self) -> None:
        r = _run(
            "parse-time-range",
            "--value", "garbage",
            "--now", "2026-06-10T15:30:00+08:00",
        )
        assert r.returncode == 2
        err = json.loads(r.stderr)
        assert err["error"] == "invalid_time_range"

    def test_missing_timezone(self) -> None:
        r = _run(
            "parse-time-range",
            "--value", "relative:today",
            "--now", "2026-06-10T15:30:00",
        )
        assert r.returncode == 2
        err = json.loads(r.stderr)
        assert err["error"] == "missing_timezone"


# ======================================================================
# project
# ======================================================================


class TestProject:
    def test_delete_last_7_days(self) -> None:
        r = _run(
            "project",
            "--decision-file", str(FIXTURES / "delete_last_7_days.json"),
            "--now", "2026-06-10T15:30:00+08:00",
        )
        assert r.returncode == 0
        out = json.loads(r.stdout)
        assert out["kind"] == "new_selection"
        assert "query" in out
        assert "query_hash" in out
        assert out["query_hash"].startswith("sha256:")

    def test_low_verdict(self) -> None:
        r = _run(
            "project",
            "--decision-file", str(FIXTURES / "low_verdict.json"),
            "--now", "2026-06-10T15:30:00+08:00",
        )
        assert r.returncode == 0
        out = json.loads(r.stdout)
        assert out["kind"] == "clarification"
        assert out["reason"] == "verdict_low"

    def test_file_not_found(self) -> None:
        r = _run(
            "project",
            "--decision-file", "nonexistent.json",
            "--now", "2026-06-10T15:30:00+08:00",
        )
        assert r.returncode == 2
        err = json.loads(r.stderr)
        assert err["error"] == "file_not_found"

    def test_existing_selection(self) -> None:
        r = _run(
            "project",
            "--decision-file", str(FIXTURES / "active_reference.json"),
            "--now", "2026-06-10T15:30:00+08:00",
            "--active-selection-id", "sel_test_001",
        )
        assert r.returncode == 0
        out = json.loads(r.stdout)
        assert out["kind"] == "existing_selection"
        assert out["selection_id"] == "sel_test_001"

    def test_stdout_is_stable_json(self) -> None:
        a = _run(
            "project",
            "--decision-file", str(FIXTURES / "delete_last_7_days.json"),
            "--now", "2026-06-10T15:30:00+08:00",
        )
        b = _run(
            "project",
            "--decision-file", str(FIXTURES / "delete_last_7_days.json"),
            "--now", "2026-06-10T15:30:00+08:00",
        )
        assert json.loads(a.stdout) == json.loads(b.stdout)
