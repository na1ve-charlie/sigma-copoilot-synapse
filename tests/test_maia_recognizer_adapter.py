from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from themis import IntentDecision, IntentMatch, IntentSlot, RecognitionVerdict

from maia import MaiaRecognizer, RecognitionReport, build_maia_recognizer_from_config
from maia.recognition import adapter as adapter_module
from maia.recognition.time_range import TimeRangeExpr, TimeRangeKind


class FakeRecognizer:
    def __init__(self, decision: IntentDecision) -> None:
        self.decision = decision
        self.messages: list[str] = []
        self.resolvers: list[Any | None] = []

    async def recognize(
        self,
        message: str,
        *,
        resolver: Any | None = None,
    ) -> IntentDecision:
        self.messages.append(message)
        self.resolvers.append(resolver)
        return self.decision


class FakeBusinessIntentRecognizer:
    def __init__(
        self,
        entries: list[Any],
        llm: Any,
        *,
        resolver: Any | None = None,
        config: Any | None = None,
        tree_prompt: Any | None = None,
    ) -> None:
        self.entries = entries
        self.llm = llm
        self.resolver = resolver
        self.config = config
        self.tree_prompt = tree_prompt

    async def recognize(
        self,
        message: str,
        *,
        resolver: Any | None = None,
    ) -> IntentDecision:
        return IntentDecision(verdict=RecognitionVerdict.CLEAR, intents=())


class FakeTimeRangeExtractor:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    async def extract_time_range(self, *, message: str, target: str) -> Any:
        self.calls.append((message, target))
        return self.responses[target]


def run(coro):
    return asyncio.run(coro)


def test_maia_recognizer_maps_public_themis_fields_to_report() -> None:
    decision = IntentDecision(
        verdict=RecognitionVerdict.CLEAR,
        intents=(
            IntentMatch(
                name="task.nvh.context_management.switch_sensor",
                score=0.95,
                slots=IntentSlot(
                    action="remove",
                    entity_type="sensor",
                    target="Vib1",
                    slot_valid=True,
                ),
            ),
            IntentMatch(
                name="task.nvh.context_management.switch_sensor",
                score=0.95,
                slots=IntentSlot(
                    action="add",
                    entity_type="sensor",
                    target="Vib2",
                    slot_valid=True,
                ),
            ),
            IntentMatch(
                name="task.nvh.data_delete",
                score=0.88,
                slots=IntentSlot(),
            ),
        ),
    )
    recognizer = FakeRecognizer(decision)
    resolver = object()

    report = run(
        MaiaRecognizer(recognizer).recognize(
            "删除上面这些数据",
            resolver=resolver,
        )
    )

    assert recognizer.messages == ["删除上面这些数据"]
    assert recognizer.resolvers == [resolver]
    assert isinstance(report, RecognitionReport)
    assert report.model_dump(mode="json") == {
        "message": "删除上面这些数据",
        "verdict": "clear",
        "requires_confirmation": False,
        "degraded": False,
        "intents": [
            {
                "name": "task.nvh.context_management.switch_sensor",
                "score": 0.95,
                "slots": {
                    "action": "remove",
                    "entity_type": "sensor",
                    "target": "Vib1",
                    "slot_valid": True,
                },
            },
            {
                "name": "task.nvh.context_management.switch_sensor",
                "score": 0.95,
                "slots": {
                    "action": "add",
                    "entity_type": "sensor",
                    "target": "Vib2",
                    "slot_valid": True,
                },
            },
            {
                "name": "task.nvh.data_delete",
                "score": 0.88,
                "slots": {},
            },
        ],
        "action_intents": [
            {
                "name": "task.nvh.data_delete",
                "score": 0.88,
            }
        ],
        "slot_operations": [
            {
                "intent": "task.nvh.context_management.switch_sensor",
                "score": 0.95,
                "action": ["remove", "add"],
                "entity_type": "sensor",
                "target": ["Vib1", "Vib2"],
                "slot_valid": [True, True],
            }
        ],
        "diagnostics": {},
    }


def test_maia_recognizer_can_include_public_diagnostics() -> None:
    decision = IntentDecision(
        verdict=RecognitionVerdict.AMBIGUOUS,
        intents=(),
    )

    report = run(
        MaiaRecognizer(FakeRecognizer(decision)).recognize(
            "这个到底是哪个意图",
            include_diagnostics=True,
        )
    )

    assert report.verdict == "ambiguous"
    assert report.requires_confirmation is True
    assert report.diagnostics == {
        "top_candidate": None,
        "runner_up": None,
        "degraded": False,
    }


def test_maia_recognizer_normalizes_public_slot_targets() -> None:
    decision = IntentDecision(
        verdict=RecognitionVerdict.CLEAR,
        intents=(
            IntentMatch(
                name="task.nvh.selection.set_summary_result",
                score=0.95,
                slots=IntentSlot(
                    action="replace",
                    entity_type="summary_result",
                    target="OK",
                    slot_valid=True,
                ),
            ),
            IntentMatch(
                name="task.nvh.selection.set_time_range",
                score=0.94,
                slots=IntentSlot(
                    action="replace",
                    entity_type="time_range",
                    target="2026-06-12前",
                    slot_valid=True,
                ),
            ),
        ),
    )

    report = run(MaiaRecognizer(FakeRecognizer(decision)).recognize("show records"))

    assert report.intents[0].slots["target"] == "合格"
    assert report.intents[1].slots["target"] == "end=2026-06-12 00:00:00"
    assert report.slot_operations[0].target == "合格"
    assert report.slot_operations[1].target == "end=2026-06-12 00:00:00"


def test_build_maia_recognizer_from_config_uses_public_themis_builder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    intents_dir = tmp_path / "intents"
    intents_dir.mkdir()
    (intents_dir / "actions.yaml").write_text(
        "intents:\n"
        "- name: task.nvh.data_delete\n"
        "  domain: nvh.data_management\n"
        "  embed_text: 删除数据\n",
        encoding="utf-8",
    )
    (intents_dir / "chat.yaml").write_text(
        "intents:\n"
        "- name: chat.nvh.capabilities\n"
        "  domain: nvh.chat\n"
        "  embed_text: 你能做什么\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "recognition.yaml"
    config_path.write_text(
        "\n".join(
            [
                "recognition:",
                "  intents_path: intents",
                "  tree_prompt_path: tree_prompt.yaml",
                "  report_contract_path: report.yaml",
                "  llm:",
                "    model: test-model",
                "  themis:",
                "    alpha: 0.2",
                "    delta: 0.15",
                "    min_intent_score: 0.6",
                "    build_index_on_init: true",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "tree_prompt.yaml").write_text(
        "\n".join(
            [
                "template: |",
                "  TEMPLATE",
                "  {tree_text}",
                "  TYPES {entity_types}",
                "  {examples_section}",
                "  MSG {message}",
                "examples_path: prompt_examples.yaml",
                "example_rendering:",
                "  max_examples: 4",
                "  group_selection: message_aware",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "prompt_examples.yaml").write_text(
        "\n".join(
            [
                "examples:",
                '  - input: "switch {sensor}"',
                "    variables:",
                "      sensor:",
                '        entity_type: "sensor"',
                "        pick: 0",
                "    intents:",
                '      - intent: "task.demo.switch_sensor"',
                '        action: "replace"',
                '        entity_type: "sensor"',
                '        target_from: "sensor"',
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "calibration_cases.yaml").write_text(
        "\n".join(
            [
                "cases:",
                '  - message: "find fail records from last week"',
                "    expected:",
                '      - "task.demo.selection.set_time_range"',
                '      - "task.demo.selection.set_summary_result"',
                '      - "task.demo.record_search"',
                "    slots:",
                '      - {action: "replace", entity_type: "time_range", target: "last_week"}',
                '      - {action: "replace", entity_type: "summary_result", target: "FAIL"}',
                "      - {}",
                '  - message: "latest 5 only"',
                '    expected: "task.demo.selection.set_latest_n"',
                '    slots: {action: "replace", entity_type: "latest_n", target: "5"}',
            ]
        ),
        encoding="utf-8",
    )

    llm = object()
    created: dict[str, Any] = {}

    def fake_builder(
        entries: list[Any],
        llm_value: Any,
        *,
        resolver: Any | None = None,
        config: Any | None = None,
        tree_prompt: Any | None = None,
    ) -> FakeBusinessIntentRecognizer:
        created["entries"] = entries
        created["llm"] = llm_value
        created["resolver"] = resolver
        created["config"] = config
        created["tree_prompt"] = tree_prompt
        return FakeBusinessIntentRecognizer(
            entries,
            llm_value,
            resolver=resolver,
            config=config,
            tree_prompt=tree_prompt,
        )

    monkeypatch.setattr(adapter_module, "BusinessIntentRecognizer", fake_builder)

    recognizer = build_maia_recognizer_from_config(
        config_path=config_path,
        llm=llm,
        resolver="resolver",
    )

    assert isinstance(recognizer, MaiaRecognizer)
    assert {entry.name for entry in created["entries"]} == {
        "task.nvh.data_delete",
        "chat.nvh.capabilities",
    }
    assert created["llm"] is llm
    assert created["resolver"] == "resolver"
    assert created["config"].alpha == 0.2
    assert created["config"].delta == 0.15
    assert created["config"].min_intent_score == 0.6
    assert created["config"].build_index_on_init is True
    assert created["tree_prompt"] is not None
    assert created["tree_prompt"].template.startswith("TEMPLATE")
    assert created["tree_prompt"].example_rendering.max_examples == 4
    assert created["tree_prompt"].examples[0].input == "find fail records from last week"
    assert [item.intent for item in created["tree_prompt"].examples[0].intents] == [
        "task.demo.selection.set_time_range",
        "task.demo.selection.set_summary_result",
        "task.demo.record_search",
    ]
    assert created["tree_prompt"].examples[1].input == "switch {sensor}"
    assert set(created["tree_prompt"].entity_types) == {
        "latest_n",
        "sensor",
        "summary_result",
        "time_range",
    }
    assert created["tree_prompt"].resolver_entity_types == ("sensor",)


def test_build_maia_recognizer_from_config_builds_llm_from_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    intents_dir = tmp_path / "intents"
    intents_dir.mkdir()
    (intents_dir / "actions.yaml").write_text(
        "intents:\n"
        "- name: task.nvh.data_delete\n"
        "  domain: nvh.data_management\n"
        "  embed_text: 删除数据\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "recognition.yaml"
    config_path.write_text(
        "\n".join(
            [
                "recognition:",
                "  intents_path: intents",
                "  report_contract_path: report.yaml",
                "  llm:",
                "    model: test-model",
                "    base_url: http://localhost:1234/v1",
                "    api_key: not-needed",
                "    temperature: 0.2",
                "    max_tokens: 900",
                "    retries: 4",
            ]
        ),
        encoding="utf-8",
    )

    llm_kwargs: dict[str, Any] = {}

    def fake_llm(**kwargs: Any) -> object:
        llm_kwargs.update(kwargs)
        return "llm-client"

    monkeypatch.setattr(adapter_module, "OpenAICompatibleLLM", fake_llm)
    monkeypatch.setattr(
        adapter_module,
        "BusinessIntentRecognizer",
        lambda entries, llm, **kwargs: FakeBusinessIntentRecognizer(entries, llm, **kwargs),
    )

    recognizer = build_maia_recognizer_from_config(config_path=config_path)

    assert isinstance(recognizer, MaiaRecognizer)
    assert llm_kwargs == {
        "model": "test-model",
        "base_url": "http://localhost:1234/v1",
        "api_key": "not-needed",
        "temperature": 0.2,
        "max_tokens": 900,
        "retries": 4,
    }


def test_build_maia_recognizer_from_config_requires_intent_yaml_files(
    tmp_path: Path,
) -> None:
    intents_dir = tmp_path / "intents"
    intents_dir.mkdir()
    config_path = tmp_path / "recognition.yaml"
    config_path.write_text(
        "\n".join(
            [
                "recognition:",
                "  intents_path: intents",
                "  report_contract_path: report.yaml",
                "  llm:",
                "    model: test-model",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError, match="no Maia intent YAML files found"):
        build_maia_recognizer_from_config(config_path=config_path, llm=object())


def test_maia_recognizer_revalidates_time_range_from_business_normalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from maia.recognition import normalization as normalization_module

    monkeypatch.setattr(
        normalization_module,
        "_now",
        lambda: normalization_module.datetime(2026, 6, 12, 0, 0, 0),
    )
    decision = IntentDecision(
        verdict=RecognitionVerdict.CLEAR,
        intents=(
            IntentMatch(
                name="task.nvh.selection.set_time_range",
                score=0.95,
                slots=IntentSlot(
                    action="replace",
                    entity_type="time_range",
                    target="\u6700\u8fd11\u5468",
                    slot_valid=False,
                ),
            ),
        ),
    )

    report = run(MaiaRecognizer(FakeRecognizer(decision)).recognize("show records"))

    assert report.intents[0].slots["target"] == "start=2026-06-05 00:00:00; end=2026-06-12 00:00:00"
    assert report.intents[0].slots["slot_valid"] is True
    assert report.slot_operations[0].target == "start=2026-06-05 00:00:00; end=2026-06-12 00:00:00"
    assert report.slot_operations[0].slot_valid is True


def test_maia_recognizer_does_not_call_time_extractor_for_rule_supported_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from maia.recognition import normalization as normalization_module

    monkeypatch.setattr(
        normalization_module,
        "_now",
        lambda: normalization_module.datetime(2026, 6, 12, 0, 0, 0),
    )
    extractor = FakeTimeRangeExtractor({})
    decision = IntentDecision(
        verdict=RecognitionVerdict.CLEAR,
        intents=(
            IntentMatch(
                name="task.nvh.selection.set_time_range",
                score=0.95,
                slots=IntentSlot(
                    action="replace",
                    entity_type="time_range",
                    target="最近1周",
                    slot_valid=False,
                ),
            ),
        ),
    )

    report = run(
        MaiaRecognizer(
            FakeRecognizer(decision),
            time_range_extractor=extractor,
        ).recognize("查最近一周")
    )

    assert extractor.calls == []
    assert report.slot_operations[0].target == "start=2026-06-05 00:00:00; end=2026-06-12 00:00:00"
    assert report.slot_operations[0].slot_valid is True


def test_maia_recognizer_uses_time_extractor_for_natural_language_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from maia.recognition import normalization as normalization_module

    monkeypatch.setattr(
        normalization_module,
        "_now",
        lambda: normalization_module.datetime(2026, 6, 16, 13, 20, 30),
    )
    extractor = FakeTimeRangeExtractor(
        {
            "昨天下午16:14之后": TimeRangeExpr(
                TimeRangeKind.AFTER_DATETIME,
                date_ref="YESTERDAY",
                time="16:14:00",
            )
        }
    )
    decision = IntentDecision(
        verdict=RecognitionVerdict.CLEAR,
        intents=(
            IntentMatch(
                name="task.nvh.selection.set_time_range",
                score=0.95,
                slots=IntentSlot(
                    action="replace",
                    entity_type="time_range",
                    target="昨天下午16:14之后",
                    slot_valid=False,
                ),
            ),
        ),
    )

    report = run(
        MaiaRecognizer(
            FakeRecognizer(decision),
            time_range_extractor=extractor,
        ).recognize("我想看昨天下午16:14之后的测试记录")
    )

    assert extractor.calls == [("我想看昨天下午16:14之后的测试记录", "昨天下午16:14之后")]
    assert report.intents[0].slots["target"] == "start=2026-06-15 16:14:00"
    assert report.intents[0].slots["slot_valid"] is True
    assert report.slot_operations[0].target == "start=2026-06-15 16:14:00"
    assert report.slot_operations[0].slot_valid is True


def test_maia_recognizer_marks_ambiguous_time_extractor_result_invalid() -> None:
    extractor = FakeTimeRangeExtractor(
        {"前几天": {"kind": "AMBIGUOUS", "source_text": "前几天"}}
    )
    decision = IntentDecision(
        verdict=RecognitionVerdict.CLEAR,
        intents=(
            IntentMatch(
                name="task.nvh.selection.set_time_range",
                score=0.95,
                slots=IntentSlot(
                    action="replace",
                    entity_type="time_range",
                    target="前几天",
                    slot_valid=False,
                ),
            ),
        ),
    )

    report = run(
        MaiaRecognizer(
            FakeRecognizer(decision),
            time_range_extractor=extractor,
        ).recognize("查前几天的不合格记录")
    )

    assert extractor.calls == [("查前几天的不合格记录", "前几天")]
    assert report.intents[0].slots["target"] == "前几天"
    assert report.intents[0].slots["slot_valid"] is False
    assert report.slot_operations[0].target == "前几天"
    assert report.slot_operations[0].slot_valid is False


@pytest.mark.parametrize(
    ("raw", "expr", "expected"),
    [
        (
            "大前天的记录",
            {"kind": "RELATIVE_DAY", "source_text": "大前天", "offset_days": -3, "confidence": 0.9},
            "start=2026-06-13 00:00:00; end=2026-06-13 23:59:59",
        ),
        (
            "上周六的数据",
            {"kind": "CALENDAR_WEEKDAY", "source_text": "上周六", "week_offset": -1, "weekday": 6, "confidence": 0.9},
            "start=2026-06-13 00:00:00; end=2026-06-13 23:59:59",
        ),
    ],
)
def test_maia_recognizer_accepts_valid_structured_time_extractor_results(
    raw: str,
    expr: dict[str, object],
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from maia.recognition import normalization as normalization_module

    monkeypatch.setattr(
        normalization_module,
        "_now",
        lambda: normalization_module.datetime(2026, 6, 16, 13, 20, 30),
    )
    extractor = FakeTimeRangeExtractor({raw: expr})
    decision = IntentDecision(
        verdict=RecognitionVerdict.CLEAR,
        intents=(
            IntentMatch(
                name="task.nvh.selection.set_time_range",
                score=0.95,
                slots=IntentSlot(
                    action="replace",
                    entity_type="time_range",
                    target=raw,
                    slot_valid=False,
                ),
            ),
        ),
    )

    report = run(
        MaiaRecognizer(
            FakeRecognizer(decision),
            time_range_extractor=extractor,
        ).recognize(f"查{raw}")
    )

    assert extractor.calls == [(f"查{raw}", raw)]
    assert report.intents[0].slots["target"] == expected
    assert report.intents[0].slots["slot_valid"] is True
    assert report.slot_operations[0].target == expected
    assert report.slot_operations[0].slot_valid is True


def test_maia_recognizer_marks_low_confidence_time_extractor_result_invalid() -> None:
    extractor = FakeTimeRangeExtractor(
        {
            "上上个周二": {
                "kind": "CALENDAR_WEEKDAY",
                "source_text": "上上个周二",
                "week_offset": -2,
                "weekday": 2,
                "confidence": 0.7,
            }
        }
    )
    decision = IntentDecision(
        verdict=RecognitionVerdict.CLEAR,
        intents=(
            IntentMatch(
                name="task.nvh.selection.set_time_range",
                score=0.95,
                slots=IntentSlot(
                    action="replace",
                    entity_type="time_range",
                    target="上上个周二",
                    slot_valid=False,
                ),
            ),
        ),
    )

    report = run(
        MaiaRecognizer(
            FakeRecognizer(decision),
            time_range_extractor=extractor,
        ).recognize("查上上个周二")
    )

    assert extractor.calls == [("查上上个周二", "上上个周二")]
    assert report.intents[0].slots["target"] == "上上个周二"
    assert report.intents[0].slots["slot_valid"] is False
    assert report.slot_operations[0].target == "上上个周二"
    assert report.slot_operations[0].slot_valid is False


def test_maia_recognizer_revalidates_summary_result_alias_from_business_normalization() -> None:
    decision = IntentDecision(
        verdict=RecognitionVerdict.CLEAR,
        intents=(
            IntentMatch(
                name="task.nvh.selection.set_summary_result",
                score=0.95,
                slots=IntentSlot(
                    action="replace",
                    entity_type="summary_result",
                    target="NG",
                    slot_valid=False,
                ),
            ),
        ),
    )

    report = run(MaiaRecognizer(FakeRecognizer(decision)).recognize("show records"))

    assert report.intents[0].slots["target"] == "不合格"
    assert report.intents[0].slots["slot_valid"] is True
    assert report.slot_operations[0].target == "不合格"
    assert report.slot_operations[0].slot_valid is True


def test_maia_recognizer_fills_summary_result_from_message_and_drops_invalid_indicator() -> None:
    decision = IntentDecision(
        verdict=RecognitionVerdict.CLEAR,
        intents=(
            IntentMatch(
                name="task.nvh.selection.set_indicator",
                score=0.95,
                slots=IntentSlot(
                    action="replace",
                    entity_type="indicator",
                    target="界限值",
                    slot_valid=False,
                ),
            ),
        ),
    )

    report = run(MaiaRecognizer(FakeRecognizer(decision)).recognize("我想查看界限值未设置的测试记录"))

    assert [operation.entity_type for operation in report.slot_operations] == ["summary_result"]
    assert report.slot_operations[0].action == "replace"
    assert report.slot_operations[0].target == "未设置界限值"
    assert report.slot_operations[0].slot_valid is True
