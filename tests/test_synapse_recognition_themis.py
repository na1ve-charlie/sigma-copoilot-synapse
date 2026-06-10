from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from themis import IntentDecision, IntentMatch, IntentSlot, RecognitionVerdict

from synapse.engine import TurnContext
from synapse.recognition import CANDIDATE_CATALOG_ARTIFACT, CandidateCatalog
from synapse.recognition import themis as themis_module
from synapse.recognition.themis import (
    ThemisRecognitionStep,
    build_themis_recognizer,
    build_themis_recognizer_from_config,
)
from synapse.turns import TurnRequest


class FakeLLM:
    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        return "{}"


SENSORS_INTENT = "inquiry.nvh.resolver_query.sensors"
SEGMENTS_INTENT = "inquiry.nvh.resolver_query.test_segments"
INDICATORS_INTENT = "inquiry.nvh.resolver_query.indicators"


class ExampleAwareLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        message = _prompt_message(prompt)
        if message == "当前有哪些传感器":
            return _llm_reply(SENSORS_INTENT)
        if message == "当前有哪些指标":
            return _llm_reply(INDICATORS_INTENT)
        if (
            message == "当前有哪些传感器、测试段"
            and 'Input: "当前有哪些传感器、测试段"' in prompt
        ):
            return _llm_reply(SENSORS_INTENT, SEGMENTS_INTENT)
        if (
            message == "当前有哪些传感器、测试段、指标"
            and 'Input: "当前有哪些传感器、测试段、指标"' in prompt
        ):
            return _llm_reply(SENSORS_INTENT, SEGMENTS_INTENT, INDICATORS_INTENT)
        return _llm_reply(SENSORS_INTENT)


class FakeRecognizer:
    def __init__(self, decision: Any) -> None:
        self.decision = decision
        self.messages: list[str] = []
        self.resolvers: list[Any | None] = []

    async def recognize(
        self,
        message: str,
        *,
        resolver: Any | None = None,
    ) -> Any:
        self.messages.append(message)
        self.resolvers.append(resolver)
        return self.decision


def run(coro):
    return asyncio.run(coro)


def ctx() -> TurnContext:
    return TurnContext.from_request(
        TurnRequest(session_id="s1", message="查频谱")
    )


def test_themis_step_stores_clear_decision_without_plan() -> None:
    decision = SimpleNamespace(verdict="clear", action_intents=(), slot_operations=())
    recognizer = FakeRecognizer(decision)

    result = run(ThemisRecognitionStep(recognizer).run(ctx()))

    assert recognizer.messages == ["查频谱"]
    assert recognizer.resolvers == [None]
    assert result.artifacts["intent_decision"] is decision
    assert result.plan is None


def test_themis_step_uses_request_candidate_catalog_resolver() -> None:
    decision = SimpleNamespace(verdict="clear", action_intents=(), slot_operations=())
    recognizer = FakeRecognizer(decision)
    context = ctx().with_artifact(
        CANDIDATE_CATALOG_ARTIFACT,
        CandidateCatalog.from_mapping(
            {"sensor": [{"value": "VibX", "label": "Seat X"}]}
        ),
    )

    result = run(ThemisRecognitionStep(recognizer).run(context))

    assert result.plan is None
    assert run(recognizer.resolvers[0].resolve("sensor")) == [
        {"value": "VibX", "label": "Seat X"}
    ]


def test_themis_step_returns_reply_for_low_confidence() -> None:
    decision = SimpleNamespace(verdict="low", action_intents=(), slot_operations=())

    result = run(ThemisRecognitionStep(FakeRecognizer(decision)).run(ctx()))

    assert result.artifacts["intent_decision"] is decision
    assert result.plan is not None
    assert result.plan["kind"] == "reply"
    assert result.plan["message"] == "我还没有识别出明确的业务意图。"


def test_themis_step_returns_clarify_for_ambiguous_intent() -> None:
    decision = SimpleNamespace(
        verdict=SimpleNamespace(value="ambiguous"),
        action_intents=(),
        slot_operations=(),
    )

    result = run(ThemisRecognitionStep(FakeRecognizer(decision)).run(ctx()))

    assert result.artifacts["intent_decision"] is decision
    assert result.plan is not None
    assert result.plan["kind"] == "clarify"
    assert result.plan["reason"] == "ambiguous_intent"
    assert result.plan["message"] == "我还不能确定你想执行哪个业务意图。"


def test_themis_step_keeps_resolver_query_without_target_as_action_intent() -> None:
    decision = IntentDecision(
        verdict=RecognitionVerdict.CLEAR,
        intents=(
            IntentMatch(
                name=SENSORS_INTENT,
                score=0.95,
                slots=IntentSlot(entity_type="sensor"),
            ),
        ),
    )

    result = run(ThemisRecognitionStep(FakeRecognizer(decision)).run(ctx()))
    normalized = result.artifacts["intent_decision"]

    assert tuple(intent.name for intent in normalized.action_intents) == (
        SENSORS_INTENT,
    )
    assert normalized.slot_operations == ()
    assert normalized.intents[0].slots == IntentSlot()
    assert result.plan is None


def test_themis_step_duplicates_targeted_resolver_query_as_action_intent() -> None:
    decision = IntentDecision(
        verdict=RecognitionVerdict.CLEAR,
        intents=(
            IntentMatch(
                name=INDICATORS_INTENT,
                score=0.95,
                slots=IntentSlot(entity_type="indicator", target="频谱"),
            ),
        ),
    )

    result = run(ThemisRecognitionStep(FakeRecognizer(decision)).run(ctx()))
    normalized = result.artifacts["intent_decision"]

    assert tuple(intent.name for intent in normalized.action_intents) == (
        INDICATORS_INTENT,
    )
    assert len(normalized.slot_operations) == 1
    assert normalized.slot_operations[0].target == "频谱"
    assert result.plan is None


def test_build_themis_recognizer_initializes_from_multiple_intent_files(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.yaml"
    first.write_text(
        "intents:\n"
        "- name: task.nvh.data_observation.batch.one_dim_data\n"
        "  domain: nvh.data_observation\n"
        "  embed_text: 一维数据\n",
        encoding="utf-8",
    )
    second = tmp_path / "second.yaml"
    second.write_text(
        "intents:\n"
        "- name: chat.nvh.capabilities\n"
        "  domain: nvh.chat\n"
        "  embed_text: 你能做什么\n",
        encoding="utf-8",
    )

    recognizer = build_themis_recognizer(
        llm=FakeLLM(), intent_config_paths=(first, second)
    )

    assert hasattr(recognizer, "recognize")


def test_multi_resolver_query_sensors_and_segments_are_recognized_from_examples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = ExampleAwareLLM()
    monkeypatch.setattr(themis_module, "OpenAICompatibleLLM", lambda **_: llm)
    recognizer = build_themis_recognizer_from_config(
        config_path=_example_config(tmp_path),
    )

    decision = run(recognizer.recognize("当前有哪些传感器、测试段"))

    assert decision.verdict == RecognitionVerdict.CLEAR
    assert tuple(intent.name for intent in decision.intents) == (
        SENSORS_INTENT,
        SEGMENTS_INTENT,
    )
    assert llm.prompts
    assert 'Input: "当前有哪些传感器、测试段"' in llm.prompts[0]


def test_multi_resolver_query_with_indicators_is_recognized_from_examples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = ExampleAwareLLM()
    monkeypatch.setattr(themis_module, "OpenAICompatibleLLM", lambda **_: llm)
    recognizer = build_themis_recognizer_from_config(
        config_path=_example_config(tmp_path),
    )

    decision = run(recognizer.recognize("当前有哪些传感器、测试段、指标"))

    assert decision.verdict == RecognitionVerdict.CLEAR
    assert tuple(intent.name for intent in decision.intents) == (
        SENSORS_INTENT,
        SEGMENTS_INTENT,
        INDICATORS_INTENT,
    )
    assert llm.prompts
    assert 'Input: "当前有哪些传感器、测试段、指标"' in llm.prompts[0]


def test_single_entity_resolver_query_recognition_stays_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = ExampleAwareLLM()
    monkeypatch.setattr(themis_module, "OpenAICompatibleLLM", lambda **_: llm)
    recognizer = build_themis_recognizer_from_config(
        config_path=_example_config(tmp_path),
    )

    sensor_decision = run(recognizer.recognize("当前有哪些传感器"))
    indicator_decision = run(recognizer.recognize("当前有哪些指标"))

    assert sensor_decision.verdict == RecognitionVerdict.CLEAR
    assert tuple(intent.name for intent in sensor_decision.intents) == (
        SENSORS_INTENT,
    )
    assert indicator_decision.verdict == RecognitionVerdict.CLEAR
    assert tuple(intent.name for intent in indicator_decision.intents) == (
        INDICATORS_INTENT,
    )


def _example_config(tmp_path: Path) -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    config_path = tmp_path / "copilot.yaml"
    config_path.write_text(
        "\n".join(
            [
                "recognition:",
                f"  intents_path: \"{(repo_root / 'configs' / 'themis' / 'intents').as_posix()}\"",
                f"  tree_prompt_path: \"{(repo_root / 'configs' / 'themis' / 'tree_prompt.yaml').as_posix()}\"",
                "  llm:",
                "    model: test-model",
                "    api_key: test-key",
                "  themis:",
                "    alpha: 0.1",
                "    delta: 0.1",
                "    min_intent_score: 0.5",
                "    build_index_on_init: false",
                "slots:",
                "  data_types:",
                "    kind: system",
                "    entity_type: data_type",
                "  sensors:",
                "    kind: system",
                "    entity_type: sensor",
                "  test_segments:",
                "    kind: system",
                "    entity_type: test_segment",
                "  indicator_names:",
                "    kind: system",
                "    entity_type: indicator",
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def _llm_reply(*intent_names: str) -> str:
    return json.dumps(
        {
            "intents": [
                {
                    "intent": intent_name,
                    "score": 1.0,
                    "action": "",
                    "entity_type": "",
                    "target": "",
                    "slot_valid": True,
                }
                for intent_name in intent_names
            ]
        },
        ensure_ascii=False,
    )


def _prompt_message(prompt: str) -> str:
    match = re.search(r'## User Message\s+"([^"]+)"', prompt)
    assert match is not None
    return match.group(1)
