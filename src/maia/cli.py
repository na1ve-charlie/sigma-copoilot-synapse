"""CLI for Maia recognition debugging and contract verification."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from maia import RecognitionReport, build_maia_recognizer_from_config
from maia.api import TurnRequest, WorkspaceContext
from maia.integrations.sigma import TestRecordClient, TestRecordSummary
from maia.recognition.resolver_loader import load_cli_resolver
from maia.runtime import ConversationStateRepository, create_maia_runtime
from maia.selection import InMemorySelectionSetRepository, SelectionSet
from maia.selection.compiler import SelectionQueryCompiler


INTERACTIVE_SEPARATOR = "-" * 40
_DECISION_LABEL_WIDTH = 23
_ACTION_NAME_WIDTH = 46


@dataclass(frozen=True)
class QueryPreview:
    report: RecognitionReport
    selection_set: SelectionSet | None
    records: tuple[TestRecordSummary, ...] = ()


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
    recognize.add_argument("--workspace-context", type=Path)
    recognize.add_argument("--show-selection-set", action="store_true")
    recognize.add_argument("--load-records", action="store_true")
    return parser


def _run_recognize(args: argparse.Namespace) -> int:
    if args.compact and not args.json:
        print("--compact requires --json", file=sys.stderr)
        return 2
    if args.load_records and args.message is None:
        print("--load-records requires --message", file=sys.stderr)
        return 2
    if args.show_selection_set and args.message is None:
        print("--show-selection-set requires --message", file=sys.stderr)
        return 2
    if args.json and (args.show_selection_set or args.load_records):
        print("preview flags are text-mode only", file=sys.stderr)
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

    if args.message is not None:
        return _run_single(
            args.message,
            resolver=resolver,
            json_output=bool(args.json),
            compact=bool(args.compact),
            include_diagnostics=bool(args.diagnostics),
            workspace_context_path=args.workspace_context,
            show_selection_set=bool(args.show_selection_set),
            load_records=bool(args.load_records),
        )
    recognizer = build_maia_recognizer_from_config()
    return _run_interactive(
        recognizer,
        resolver=resolver,
        json_output=bool(args.json),
        compact=bool(args.compact),
        include_diagnostics=bool(args.diagnostics),
    )


def _run_single(
    message: str,
    *,
    resolver: Any | None,
    json_output: bool,
    compact: bool,
    include_diagnostics: bool,
    workspace_context_path: Path | None,
    show_selection_set: bool,
    load_records: bool,
) -> int:
    preview: QueryPreview | None = None
    if show_selection_set or load_records:
        try:
            preview = build_query_preview(
                message=message,
                workspace_context_path=workspace_context_path,
                resolver=resolver,
                load_records=load_records,
            )
        except Exception as exc:
            print(f"preview failed: {exc}", file=sys.stderr)
            return 1
        report = preview.report
    else:
        report = _recognize_message(
            build_maia_recognizer_from_config(),
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
            preview=preview,
            show_selection_set=show_selection_set or load_records,
            load_records=load_records,
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


def build_query_preview(
    *,
    message: str,
    workspace_context_path: Path | None,
    resolver: Any | None,
    load_records: bool,
) -> QueryPreview:
    return asyncio.run(_build_query_preview(
        message=message,
        workspace_context=None if workspace_context_path is None else WorkspaceContext.model_validate(
            json.loads(workspace_context_path.read_text(encoding="utf-8"))
        ),
        resolver=resolver,
        load_records=load_records,
    ))


async def _build_query_preview(
    *,
    message: str,
    workspace_context: WorkspaceContext | None,
    resolver: Any | None,
    load_records: bool,
) -> QueryPreview:
    recognizer = _PreviewRecognizer(build_maia_recognizer_from_config(), resolver=resolver)
    selection_repository = InMemorySelectionSetRepository()
    record_client = TestRecordClient(base_url=os.getenv("SIGMA_BASE_URL", "http://192.168.0.65:8081"), token=os.getenv("SIGMA_TOKEN"))
    handler = create_maia_runtime(
        recognizer=recognizer,
        record_client=record_client,
        state_repository=ConversationStateRepository(),
        selection_repository=selection_repository,
    )
    response = await handler.handle_turn(
        TurnRequest(
            session_id="cli-preview",
            message=message,
            workspace_context=workspace_context,
        )
    )
    selection_set_id = response.plan.data.get("selection_set_id") if response.plan.kind == "reply" else None
    selection_set = selection_repository.get(selection_set_id) if isinstance(selection_set_id, str) and selection_set_id.strip() else None
    records: tuple[TestRecordSummary, ...] = ()
    if load_records and selection_set is not None:
        records = (
            await SelectionQueryCompiler(record_client).compile(
                {
                    "expression": selection_set.expression,
                    "sort": selection_set.sort,
                    "limit": selection_set.limit,
                },
                workspace_context=workspace_context,
            )
        ).records
    if recognizer.last_report is None:
        raise RuntimeError("preview recognizer did not capture a report")
    return QueryPreview(report=recognizer.last_report, selection_set=selection_set, records=records)


def _render_output(
    report: RecognitionReport,
    *,
    json_output: bool,
    compact: bool,
    include_diagnostics: bool,
    preview: QueryPreview | None = None,
    show_selection_set: bool = False,
    load_records: bool = False,
) -> str:
    if json_output:
        return render_report_json(report, compact=compact)
    sections = [render_report(report, include_diagnostics=include_diagnostics)]
    if show_selection_set:
        sections.append(_render_selection_set(preview.selection_set if preview else None))
    if load_records:
        sections.append(_render_loaded_records(() if preview is None else preview.records))
    return "\n\n".join(sections)


def _read_prompted_line(prompt: str) -> str:
    return input(prompt)


def _write_stdout(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.write("\n")
    sys.stdout.flush()


class _PreviewRecognizer:
    def __init__(self, recognizer: Any, *, resolver: Any | None) -> None:
        self._recognizer = recognizer
        self._resolver = resolver
        self.last_report: RecognitionReport | None = None

    async def recognize(
        self,
        message: str,
        *,
        resolver: Any | None = None,
        include_diagnostics: bool = False,
    ) -> RecognitionReport:
        report = await self._recognizer.recognize(
            message,
            resolver=self._resolver if resolver is None else resolver,
            include_diagnostics=include_diagnostics,
        )
        self.last_report = report
        return report


def _render_selection_set(selection_set: SelectionSet | None) -> str:
    lines = ["Selection Set"]
    if selection_set is None:
        lines.append("  (none)")
        return "\n".join(lines)
    lines.extend(
        [
            _decision_line("ID", selection_set.selection_set_id),
            _decision_line("Operation", selection_set.derived_operation),
            _decision_line("Record count", str(selection_set.record_count)),
            _decision_line(
                "Sort",
                "(none)"
                if not selection_set.sort
                else ", ".join(f"{item.field}:{item.direction}" for item in selection_set.sort),
            ),
            _decision_line("Expression", selection_set.expression.model_dump_json()),
        ]
    )
    return "\n".join(lines)


def _render_loaded_records(records: Sequence[TestRecordSummary]) -> str:
    lines = ["Loaded Records"]
    if not records:
        lines.append("  (none)")
        return "\n".join(lines)
    for index, record in enumerate(records, start=1):
        lines.append(f"  {index}. {record.record_id}  {record.serial_number or '(missing serialNo)'}")
    return "\n".join(lines)


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
