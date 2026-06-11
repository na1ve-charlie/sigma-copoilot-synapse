from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator

from maia.cli import INTERACTIVE_SEPARATOR, main, render_report
from maia.recognition import RecognitionReport


class FakeRecognizer:
    def __init__(self, reports: list[RecognitionReport]) -> None:
        self._reports = iter(reports)
        self.messages: list[str] = []
        self.resolvers: list[object | None] = []
        self.include_diagnostics: list[bool] = []

    async def recognize(
        self,
        message: str,
        **kwargs,
    ) -> RecognitionReport:
        self.messages.append(message)
        self.resolvers.append(kwargs.get("resolver"))
        self.include_diagnostics.append(bool(kwargs.get("include_diagnostics")))
        return next(self._reports)


def run(coro):
    return asyncio.run(coro)


def test_render_report_uses_sectioned_text_view() -> None:
    report = RecognitionReport(
        message="find failing records from last week",
        verdict="clear",
        requires_confirmation=False,
        degraded=False,
        action_intents=(
            {"name": "task.nvh.record_search", "score": 0.9821},
        ),
        slot_operations=(
            {
                "intent": "task.nvh.selection.set_summary_result",
                "score": 0.95,
                "action": "replace",
                "entity_type": "summary_result",
                "target": "fail",
                "slot_valid": True,
            },
            {
                "intent": "task.nvh.selection.set_time_range",
                "score": 0.94,
                "action": "replace",
                "entity_type": "time_range",
                "target": "last_week",
                "slot_valid": True,
            },
        ),
    )

    rendered = render_report(report)

    assert rendered == "\n".join(
        [
            "Input",
            "  find failing records from last week",
            "",
            "Decision",
            "  Verdict                 clear",
            "  Requires confirmation   no",
            "  Degraded                no",
            "",
            "Action Intents",
            "  1. task.nvh.record_search                         score=0.9821",
            "",
            "Slot Operations",
            "  1. summary_result",
            "     action     replace",
            "     target     fail",
            "     valid      yes",
            "  2. time_range",
            "     action     replace",
            "     target     last_week",
            "     valid      yes",
            "",
            "Diagnostics",
            "  hidden (use --diagnostics)",
        ]
    )


def test_render_report_keeps_empty_sections_visible() -> None:
    report = RecognitionReport(
        message="delete these records",
        verdict="clear",
        requires_confirmation=False,
        degraded=False,
    )

    rendered = render_report(report)

    assert "Action Intents\n  (none)" in rendered
    assert "Slot Operations\n  (none)" in rendered


def test_render_report_shows_diagnostics_when_requested() -> None:
    report = RecognitionReport(
        message="查看频谱",
        verdict="clear",
        requires_confirmation=False,
        degraded=False,
        diagnostics={
            "top_candidate": {
                "name": "task.nvh.data_observation.batch.frequency_spectrum",
                "score": 0.91,
            },
            "runner_up": None,
            "degraded": False,
        },
    )

    rendered = render_report(report, include_diagnostics=True)

    assert "hidden (use --diagnostics)" not in rendered
    assert "top_candidate" in rendered
    assert "task.nvh.data_observation.batch.frequency_spectrum" in rendered
    assert "runner_up" in rendered


def test_cli_single_message_mode_prints_report(
    monkeypatch,
    capsys,
) -> None:
    recognizer = FakeRecognizer(
        [
            RecognitionReport(
                message="show spectrum",
                verdict="clear",
                requires_confirmation=False,
                degraded=False,
                action_intents=(
                    {
                        "name": "task.nvh.data_observation.batch.frequency_spectrum",
                        "score": 0.91,
                    },
                ),
            )
        ]
    )
    monkeypatch.setattr("maia.cli.build_maia_recognizer_from_config", lambda: recognizer)

    exit_code = main(["recognize", "--message", "show spectrum"])
    output = capsys.readouterr()

    assert exit_code == 0
    assert recognizer.messages == ["show spectrum"]
    assert recognizer.include_diagnostics == [False]
    assert output.err == ""
    assert "Input\n  show spectrum" in output.out
    assert "Action Intents" in output.out
    assert INTERACTIVE_SEPARATOR not in output.out


def test_cli_single_message_mode_prints_pretty_json_with_diagnostics(
    monkeypatch,
    capsys,
) -> None:
    recognizer = FakeRecognizer(
        [
            RecognitionReport(
                message="查看频谱",
                verdict="clear",
                requires_confirmation=False,
                degraded=False,
                action_intents=(
                    {
                        "name": "task.nvh.data_observation.batch.frequency_spectrum",
                        "score": 0.91,
                    },
                ),
                diagnostics={
                    "top_candidate": {
                        "name": "task.nvh.data_observation.batch.frequency_spectrum",
                        "score": 0.91,
                    },
                    "runner_up": None,
                    "degraded": False,
                },
            )
        ]
    )
    monkeypatch.setattr("maia.cli.build_maia_recognizer_from_config", lambda: recognizer)

    exit_code = main(
        ["recognize", "--message", "查看频谱", "--json", "--diagnostics"]
    )
    output = capsys.readouterr()
    payload = json.loads(output.out)

    assert exit_code == 0
    assert recognizer.messages == ["查看频谱"]
    assert recognizer.include_diagnostics == [True]
    assert "\\u" not in output.out
    assert output.out.startswith("{\n  ")
    assert payload["message"] == "查看频谱"
    assert payload["diagnostics"]["top_candidate"]["name"] == (
        "task.nvh.data_observation.batch.frequency_spectrum"
    )


def test_cli_single_message_mode_prints_compact_json(
    monkeypatch,
    capsys,
) -> None:
    recognizer = FakeRecognizer(
        [
            RecognitionReport(
                message="查看频谱",
                verdict="clear",
                requires_confirmation=False,
                degraded=False,
            )
        ]
    )
    monkeypatch.setattr("maia.cli.build_maia_recognizer_from_config", lambda: recognizer)

    exit_code = main(["recognize", "--message", "查看频谱", "--json", "--compact"])
    output = capsys.readouterr()

    assert exit_code == 0
    assert recognizer.include_diagnostics == [False]
    assert "\n" not in output.out.strip()
    assert json.loads(output.out)["diagnostics"] == {}


def test_cli_rejects_compact_without_json(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "maia.cli.build_maia_recognizer_from_config",
        lambda: (_ for _ in ()).throw(AssertionError("should not build recognizer")),
    )

    exit_code = main(["recognize", "--message", "查看频谱", "--compact"])
    output = capsys.readouterr()

    assert exit_code == 2
    assert output.out == ""
    assert "--compact requires --json" in output.err


def test_cli_interactive_mode_prints_separator_between_reports(
    monkeypatch,
    capsys,
) -> None:
    recognizer = FakeRecognizer(
        [
            RecognitionReport(
                message="show spectrum",
                verdict="clear",
                requires_confirmation=False,
                degraded=False,
                action_intents=(
                    {
                        "name": "task.nvh.data_observation.batch.frequency_spectrum",
                        "score": 0.91,
                    },
                ),
            ),
            RecognitionReport(
                message="delete these records",
                verdict="clear",
                requires_confirmation=False,
                degraded=False,
                action_intents=(
                    {"name": "task.nvh.data_delete", "score": 0.88},
                ),
            ),
        ]
    )
    prompts: list[str] = []

    def fake_input() -> Iterator[str]:
        yield "show spectrum"
        yield "delete these records"
        raise EOFError

    inputs = fake_input()

    monkeypatch.setattr("maia.cli.build_maia_recognizer_from_config", lambda: recognizer)
    monkeypatch.setattr(
        "maia.cli._read_prompted_line",
        lambda prompt: (prompts.append(prompt), next(inputs))[1],
    )

    exit_code = main(["recognize"])
    output = capsys.readouterr()

    assert exit_code == 0
    assert recognizer.messages == ["show spectrum", "delete these records"]
    assert prompts == ["maia> ", "maia> ", "maia> "]
    assert output.out.count(INTERACTIVE_SEPARATOR) == 1
    assert "Input\n  show spectrum" in output.out
    assert "Input\n  delete these records" in output.out


def test_cli_resolver_values_are_loaded_and_passed_to_runtime_recognition(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    resolver_path = tmp_path / "resolver-values.yaml"
    resolver_path.write_text(
        "\n".join(
            [
                "values:",
                "  sensor: [Vib1, Vib2]",
                "  indicator: [Spectrum, RMS]",
            ]
        ),
        encoding="utf-8",
    )
    recognizer = FakeRecognizer(
        [
            RecognitionReport(
                message="切换到 Vib1",
                verdict="clear",
                requires_confirmation=False,
                degraded=False,
            )
        ]
    )
    monkeypatch.setattr("maia.cli.build_maia_recognizer_from_config", lambda: recognizer)

    exit_code = main(
        [
            "recognize",
            "--message",
            "切换到 Vib1",
            "--json",
            "--resolver-values",
            str(resolver_path),
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert json.loads(output.out)["message"] == "切换到 Vib1"
    assert recognizer.messages == ["切换到 Vib1"]
    assert len(recognizer.resolvers) == 1
    assert run(recognizer.resolvers[0].resolve("sensor")) == ["Vib1", "Vib2"]
