"""Offline Selection CLI (Task 14).  ``python -m synapse.selection.cli <command> ...``

Commands
--------
parse-time-range    Resolve a Themis time-range string to absolute instants.
project             Interpret a decision file and project to a RecordQuery.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from types import SimpleNamespace

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _die(status: int, error: str, message: str, **extra: str) -> None:
    payload: dict[str, object] = {"error": error, "message": message}
    payload.update(extra)
    json.dump(payload, sys.stderr, ensure_ascii=False)
    sys.stderr.write("\n")
    sys.exit(status)


def _parse_dt(raw: str) -> datetime:
    """Parse an ISO-8601 datetime string, enforcing timezone presence."""
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        _die(2, "invalid_datetime", f"Invalid datetime: {raw!r}")
    if dt.tzinfo is None:
        _die(2, "missing_timezone", "Datetime must include a timezone offset")
    return dt


def _load_json(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        _die(2, "file_not_found", f"File not found: {path}")
    except json.JSONDecodeError as exc:
        _die(2, "invalid_json", f"Invalid JSON in {path}: {exc}")


def _to_namespace(obj: Any) -> Any:
    """Recursively convert JSON dict / list to ``SimpleNamespace``."""
    if isinstance(obj, dict):
        return SimpleNamespace(
            **{k: _to_namespace(v) for k, v in obj.items()}
        )
    if isinstance(obj, list):
        return [_to_namespace(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _cmd_parse_time_range(args: argparse.Namespace) -> None:
    from synapse.selection.time_ranges import parse_time_range

    now = _parse_dt(args.now)
    try:
        cr = parse_time_range(args.value, now=now)
    except ValueError as exc:
        _die(2, "invalid_time_range", str(exc), field="record_time_range")
    print(json.dumps({
        "start": cr.start.isoformat() if cr.start else None,
        "end": cr.end.isoformat() if cr.end else None,
    }, ensure_ascii=False))
    sys.exit(0)


def _cmd_project(args: argparse.Namespace) -> None:
    from synapse.domains.data_management.selection_interpreter import (
        interpret_decision,
    )
    from synapse.domains.data_management.selection_interpreter import (
        DerivedSelectionCriteria,
        ExistingSelectionReference,
        NewSelectionCriteria,
        SelectionClarificationRequired,
    )
    from synapse.domains.data_management.selection_projector import (
        project_criteria,
    )
    from synapse.selection.normalization import normalize_query, query_hash

    raw = _load_json(args.decision_file)
    decision = _to_namespace(raw)

    now = _parse_dt(args.now)
    active = args.active_selection_id or None

    try:
        resolution = interpret_decision(
            decision, now=now, active_selection_id=active,
        )
    except ValueError as exc:
        _die(1, "interpret_error", str(exc))

    if isinstance(resolution, SelectionClarificationRequired):
        print(json.dumps({
            "kind": "clarification",
            "reason": resolution.reason,
        }, ensure_ascii=False))

    elif isinstance(resolution, ExistingSelectionReference):
        print(json.dumps({
            "kind": "existing_selection",
            "selection_id": resolution.selection_id,
        }, ensure_ascii=False))

    elif isinstance(resolution, DerivedSelectionCriteria):
        print(json.dumps({
            "kind": "derived_selection",
            "base_selection_id": resolution.base_selection_id,
        }, ensure_ascii=False))

    elif isinstance(resolution, NewSelectionCriteria):
        query = project_criteria(resolution.criteria)
        print(json.dumps({
            "kind": "new_selection",
            "query": normalize_query(query),
            "query_hash": query_hash(query),
        }, ensure_ascii=False))

    else:
        print(json.dumps({"kind": "unsupported"}, ensure_ascii=False))

    sys.exit(0)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m synapse.selection.cli",
        description="Offline Selection CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # parse-time-range
    p_tr = sub.add_parser("parse-time-range", help="Resolve a time-range string")
    p_tr.add_argument("--value", required=True, help="e.g. 'relative:last_7_days'")
    p_tr.add_argument("--now", required=True, help="ISO datetime with timezone")

    # project
    p_pj = sub.add_parser("project", help="Project a decision to a RecordQuery")
    p_pj.add_argument("--decision-file", required=True, help="Path to decision JSON")
    p_pj.add_argument("--now", required=True, help="ISO datetime with timezone")
    p_pj.add_argument("--active-selection-id", default=None, help="Optional active selection ID")

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "parse-time-range":
            _cmd_parse_time_range(args)
        elif args.command == "project":
            _cmd_project(args)
        else:
            parser.print_help()
            sys.exit(2)
    except SystemExit:
        raise
    except Exception as exc:
        _die(1, "unexpected_error", str(exc))


if __name__ == "__main__":
    main()
