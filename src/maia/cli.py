"""CLI for Maia recognition debugging and contract verification."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from maia import RecognitionReport, build_maia_recognizer_from_config
from maia.recognition.resolver_loader import load_cli_resolver


INTERACTIVE_SEPARATOR = "-" * 40
_DECISION_LABEL_WIDTH = 23
_ACTION_NAME_WIDTH = 46


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        if args.command == "recognize":
            return _run_recognize(args)
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130

    parser.print_help(sys.stderr)
    return 2


def render_report(
    report: RecognitionReport,
    *,
    include_diagnostics: bool = False,
) -> str:
    sections = [
        _render_input(report),
        _render_decision(report),
        _render_action_intents(report),
        _render_slot_operations(report),
        _render_diagnostics(report, include_diagnostics=include_diagnostics),
    ]
    return "\n\n".join(sections)


def render_report_json(
    report: RecognitionReport,
    *,
    compact: bool = False,
) -> str:
    return report.model_dump_json(indent=None if compact else 2)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="maia")
    subparsers = parser.add_subparsers(dest="command", required=True)
    recognize = subparsers.add_parser("recognize")
    recognize.add_argument("--message")
    recognize.add_argument("--json", action="store_true")
    recognize.add_argument("--compact", action="store_true")
    recognize.add_argument("--diagnostics", action="store_true")
    recognize.add_argument("--resolver-values", type=Path)
    return parser


def _run_recognize(args: argparse.Namespace) -> int:
    if args.compact and not args.json:
        print("--compact requires --json", file=sys.stderr)
        return 2

    try:
        resolver = (
            None
            if args.resolver_values is None
            else load_cli_resolver(args.resolver_values)
        )
    except Exception as exc:
        print(f"invalid resolver values: {exc}", file=sys.stderr)
        return 2

    recognizer = build_maia_recognizer_from_config()
    if args.message is not None:
        return _run_single(
            recognizer,
            args.message,
            resolver=resolver,
            json_output=bool(args.json),
            compact=bool(args.compact),
            include_diagnostics=bool(args.diagnostics),
        )
    return _run_interactive(
        recognizer,
        resolver=resolver,
        json_output=bool(args.json),
        compact=bool(args.compact),
        include_diagnostics=bool(args.diagnostics),
    )


def _run_single(
    recognizer: Any,
    message: str,
    *,
    resolver: Any | None,
    json_output: bool,
    compact: bool,
    include_diagnostics: bool,
) -> int:
    report = _recognize_message(
        recognizer,
        message,
        resolver=resolver,
        include_diagnostics=include_diagnostics,
    )
    if report is None:
        return 1
    _write_stdout(
        _render_output(
            report,
            json_output=json_output,
            compact=compact,
            include_diagnostics=include_diagnostics,
        )
    )
    return 0


def _run_interactive(
    recognizer: Any,
    *,
    resolver: Any | None,
    json_output: bool,
    compact: bool,
    include_diagnostics: bool,
) -> int:
    first_result = True
    while True:
        try:
            message = _read_prompted_line("maia> ")
        except EOFError:
            return 0
        if not message.strip():
            continue

        report = _recognize_message(
            recognizer,
            message,
            resolver=resolver,
            include_diagnostics=include_diagnostics,
        )
        if report is None:
            continue
        if not first_result and not json_output:
            _write_stdout(INTERACTIVE_SEPARATOR)
        _write_stdout(
            _render_output(
                report,
                json_output=json_output,
                compact=compact,
                include_diagnostics=include_diagnostics,
            )
        )
        first_result = False


def _recognize_message(
    recognizer: Any,
    message: str,
    *,
    resolver: Any | None,
    include_diagnostics: bool,
) -> RecognitionReport | None:
    try:
        return asyncio.run(
            recognizer.recognize(
                message,
                resolver=resolver,
                include_diagnostics=include_diagnostics,
            )
        )
    except Exception as exc:
        print(f"recognition failed: {exc}", file=sys.stderr)
        return None


def _render_output(
    report: RecognitionReport,
    *,
    json_output: bool,
    compact: bool,
    include_diagnostics: bool,
) -> str:
    if json_output:
        return render_report_json(report, compact=compact)
    return render_report(report, include_diagnostics=include_diagnostics)


def _read_prompted_line(prompt: str) -> str:
    return input(prompt)


def _write_stdout(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.write("\n")
    sys.stdout.flush()


def _render_input(report: RecognitionReport) -> str:
    return "\n".join(["Input", f"  {report.message}"])


def _render_decision(report: RecognitionReport) -> str:
    lines = [
        "Decision",
        _decision_line("Verdict", report.verdict),
        _decision_line(
            "Requires confirmation",
            "yes" if report.requires_confirmation else "no",
        ),
        _decision_line("Degraded", "yes" if report.degraded else "no"),
    ]
    return "\n".join(lines)


def _render_action_intents(report: RecognitionReport) -> str:
    lines = ["Action Intents"]
    if not report.action_intents:
        lines.append("  (none)")
        return "\n".join(lines)

    for index, intent in enumerate(report.action_intents, start=1):
        lines.append(
            f"  {index}. {intent.name:<{_ACTION_NAME_WIDTH}} score={intent.score:.4f}"
        )
    return "\n".join(lines)


def _render_slot_operations(report: RecognitionReport) -> str:
    lines = ["Slot Operations"]
    if not report.slot_operations:
        lines.append("  (none)")
        return "\n".join(lines)

    for index, operation in enumerate(report.slot_operations, start=1):
        lines.append(f"  {index}. {operation.entity_type}")
        lines.append(_slot_line("action", _display_value(operation.action)))
        lines.append(_slot_line("target", _display_value(operation.target)))
        lines.append(_slot_line("valid", _display_valid(operation.slot_valid)))
    return "\n".join(lines)


def _render_diagnostics(
    report: RecognitionReport,
    *,
    include_diagnostics: bool,
) -> str:
    if not include_diagnostics:
        return "\n".join(["Diagnostics", "  hidden (use --diagnostics)"])
    if not report.diagnostics:
        return "\n".join(["Diagnostics", "  (none)"])

    lines = ["Diagnostics"]
    for key, value in report.diagnostics.items():
        lines.extend(_render_diagnostic_item(str(key), value, indent="  "))
    return "\n".join(lines)


def _render_diagnostic_item(
    key: str,
    value: Any,
    *,
    indent: str,
) -> list[str]:
    if isinstance(value, Mapping):
        lines = [f"{indent}{key}"]
        if not value:
            lines.append(f"{indent}  (empty)")
            return lines
        for nested_key, nested_value in value.items():
            lines.extend(
                _render_diagnostic_item(
                    str(nested_key),
                    nested_value,
                    indent=f"{indent}  ",
                )
            )
        return lines
    return [f"{indent}{key}: {_display_value(value)}"]


def _decision_line(label: str, value: str) -> str:
    return f"  {label:<{_DECISION_LABEL_WIDTH}} {value}"


def _slot_line(label: str, value: str) -> str:
    return f"     {label:<10} {value}"


def _display_value(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(_display_value(item) for item in value)
    return str(value)


def _display_valid(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(_display_valid(item) for item in value)
    return "yes" if bool(value) else "no"


if __name__ == "__main__":
    raise SystemExit(main())
