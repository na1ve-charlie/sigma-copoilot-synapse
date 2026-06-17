from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from themis import IntentDecision, IntentMatch, IntentSlot, RecognitionVerdict

from maia.api import TurnRequest, WorkspaceContext
from maia.integrations.sigma.records import TestRecordPage, TestRecordSummary
from maia.recognition import MaiaRecognizer, RecognitionReport
from maia.selection import InMemorySelectionSetRepository
from maia.selection.expression import AllOf, AnyOf, Not, Predicate, parse_filter_expression


def test_runtime_returns_task_plan_without_workspace_context_and_materializes_dataset() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("r-1", day=1, product_type="A", config_version="1", system_no="SYS-1"),
        _record("r-2", day=2, product_type="A", config_version="1", system_no="SYS-1", summary_result="FAIL"),
        _record("r-3", day=3, product_type="A", config_version="1", system_no="SYS-1"),
    )
    materializer = _Materializer()
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "A"},
                    ],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        selection_materializer=materializer,
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "find A records")))

    assert response.plan.kind == "task"
    assert response.plan.intent == "task.nvh.record_search"
    assert response.plan.params == {}
    assert response.plan.message == "Found 3 records."
    assert response.plan.dataset.dataset_id == "dataset-1"
    assert response.plan.dataset.dataset_name.startswith("maia-")
    assert response.plan.dataset.record_count == 3
    assert response.plan.dataset.record_ids == ["r-1", "r-2", "r-3"]
    assert response.plan.dataset.selection_set_id.startswith("sel-")
    assert response.plan.dataset.selection_params["type"] == "A"
    assert "page" not in response.plan.dataset.selection_params
    assert "rows" not in response.plan.dataset.selection_params


def test_runtime_derives_follow_up_record_search_from_active_selection() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("r-1", day=1, product_type="A", config_version="1", system_no="SYS-1"),
        _record("r-2", day=2, product_type="A", config_version="1", system_no="SYS-1", summary_result="FAIL"),
        _record("r-3", day=3, product_type="A", config_version="1", system_no="SYS-1"),
    )
    materializer = _Materializer()
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "A"},
                    ],
                ),
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "add", "entity_type": "summary_result", "target": "FAIL"},
                    ],
                ),
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        selection_materializer=materializer,
        source_version="sigma-fixture-v1",
    )

    first = asyncio.run(handler.handle_turn(_request("s1", "find A records")))
    second = asyncio.run(handler.handle_turn(_request("s1", "only failing")))

    assert first.plan.dataset.dataset_id == "dataset-1"
    assert second.plan.dataset.dataset_id == "dataset-1"
    assert second.plan.dataset.dataset_name == first.plan.dataset.dataset_name
    assert second.plan.dataset.selection_set_id != first.plan.dataset.selection_set_id
    assert second.plan.dataset.record_count == 1
    assert second.plan.dataset.record_ids == ["r-2"]
    assert second.plan.dataset.selection_params["sumList"] == "FAIL"
    assert materializer.calls[1][0] == "dataset-1"


def test_runtime_does_not_reuse_materialized_dataset_across_sessions() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("r-1", day=1, product_type="A", config_version="1", system_no="SYS-1"),
    )
    materializer = _Materializer()
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "A"},
                    ],
                ),
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "A"},
                    ],
                ),
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        selection_materializer=materializer,
        source_version="sigma-fixture-v1",
    )

    first = asyncio.run(handler.handle_turn(_request("s1", "find A records")))
    second = asyncio.run(handler.handle_turn(_request("s2", "find A records")))

    assert first.plan.dataset.dataset_id == "dataset-1"
    assert second.plan.dataset.dataset_id == "dataset-2"
    assert materializer.calls == [
        (None, None, ("r-1",)),
        (None, None, ("r-1",)),
    ]


def test_runtime_summary_result_filling_replaces_previous_sumlist() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("r-1", day=1, product_type="A", config_version="1", system_no="SYS-1", summary_result="次异常"),
        _record("r-2", day=2, product_type="A", config_version="1", system_no="SYS-1", summary_result="不合格"),
        _record("r-3", day=3, product_type="A", config_version="1", system_no="SYS-1", summary_result="异常"),
    )
    selection_repository = InMemorySelectionSetRepository()
    handler = create_maia_runtime(
        recognizer=MaiaRecognizer(
            _SequenceThemisRecognizer(
                [
                    _decision("add", "异常"),
                    _decision("add", "异常"),
                ]
            )
        ),
        record_client=_RecordClient(records),
        selection_repository=selection_repository,
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        selection_materializer=_Materializer(),
        source_version="sigma-fixture-v1",
    )

    first = asyncio.run(handler.handle_turn(_request("s1", "我想查看次异常和不合格测试件的测试记录")))
    second = asyncio.run(handler.handle_turn(_request("s1", "我想查看不合格测试件的测试记录")))
    first_selection = selection_repository.get(first.plan.dataset.selection_set_id)
    second_selection = selection_repository.get(second.plan.dataset.selection_set_id)

    assert first.plan.dataset.record_ids == ["r-1", "r-2"]
    assert second.plan.dataset.record_ids == ["r-2"]
    assert first_selection is not None
    assert second_selection is not None
    assert _summary_result_values(first_selection.expression) == ("次异常", "不合格")
    assert _summary_result_values(second_selection.expression) == ("不合格",)


def test_runtime_pending_prompt_does_not_swallow_new_summary_result_request() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("r-1", day=1, product_type="A", config_version="1", system_no="SYS-1", summary_result="未设置界限值"),
        _record("r-2", day=2, product_type="B", config_version="1", system_no="SYS-2", summary_result="检测失败"),
        _record("r-3", day=3, product_type="C", config_version="1", system_no="SYS-3", summary_result="不合格"),
        _record("r-4", day=4, product_type="D", config_version="1", system_no="SYS-4", summary_result="合格"),
    )
    selection_repository = InMemorySelectionSetRepository()
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {
                            "action": "replace",
                            "entity_type": "summary_result",
                            "target": ("未设置界限值", "检测失败", "不合格"),
                            "slot_valid": (True, True, True),
                        },
                    ],
                ),
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "summary_result", "target": "合格"},
                    ],
                ),
            ]
        ),
        record_client=_RecordClient(records),
        selection_repository=selection_repository,
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        selection_materializer=_Materializer(),
        source_version="sigma-fixture-v1",
    )

    first = asyncio.run(handler.handle_turn(_request("s1", "我想要查看未设置界限值和检测失败以及NG的测试记录")))
    second = asyncio.run(handler.handle_turn(_request("s1", "我想要查看合格件的测试记录")))
    second_selection = selection_repository.get(second.plan.dataset.selection_set_id)

    assert first.plan.kind == "clarify"
    assert first.plan.prompts[0].id == "product_type"
    assert second.plan.kind == "task"
    assert second.plan.dataset.record_ids == ["r-4"]
    assert second_selection is not None
    assert _summary_result_values(second_selection.expression) == ("合格",)


def test_runtime_resolves_active_selection_reference_to_same_selection() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("r-1", day=1, product_type="A", config_version="1", system_no="SYS-1"),
        _record("r-2", day=2, product_type="A", config_version="1", system_no="SYS-1"),
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "A"},
                    ],
                ),
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {
                            "action": "replace",
                            "entity_type": "selection_reference",
                            "target": "active_selection",
                        },
                    ],
                ),
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        selection_materializer=_Materializer(),
        source_version="sigma-fixture-v1",
    )

    first = asyncio.run(handler.handle_turn(_request("s1", "find A records")))
    second = asyncio.run(handler.handle_turn(_request("s1", "show those records")))

    assert second.plan.dataset.selection_set_id == first.plan.dataset.selection_set_id
    assert second.plan.dataset.dataset_id == "dataset-1"
    assert second.plan.dataset.record_ids == first.plan.dataset.record_ids


def test_runtime_treats_selection_only_report_as_record_search_request() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("r-1", day=1, product_type="A", config_version="1", system_no="SYS-1"),
        _record("r-2", day=2, product_type="A", config_version="1", system_no="SYS-1"),
        _record("r-3", day=3, product_type="A", config_version="1", system_no="SYS-1"),
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=[],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "A"},
                    ],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "only A records")))

    assert response.plan.kind == "task"
    assert response.plan.dataset.record_count == 3
    assert response.plan.dataset.record_ids == ["r-1", "r-2", "r-3"]


def test_runtime_clarifies_product_type_from_filtered_records() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("r-1", day=1, product_type="A", config_version="1", system_no="SYS-1"),
        _record("r-2", day=2, product_type="A", config_version="1", system_no="SYS-1", summary_result="FAIL"),
        _record("r-4", day=4, product_type="B", config_version="1", system_no="SYS-2", summary_result="FAIL"),
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "summary_result", "target": "FAIL"},
                    ],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "show failing records")))

    assert response.plan.kind == "clarify"
    assert response.plan.reason == "missing_slots"
    assert response.plan.missing_slots == ["product_type"]
    assert response.plan.dataset.selection_params["sumList"] == "FAIL"
    assert response.plan.message == "\u5f53\u524d\u7b5b\u9009\u7684\u6d4b\u8bd5\u8bb0\u5f55\u6309\u6d4b\u8bd5\u65f6\u95f4\u5012\u5e8f\u6db5\u76d6\u4e86B\u3001A\uff0c\u8bf7\u9009\u62e9\u4f60\u8981\u89c2\u5bdf\u7684\u4ea7\u54c1\u578b\u53f7\u3002"
    assert [candidate.value for candidate in response.plan.prompts[0].candidates] == [
        "B",
        "A",
        "__ALL_PRODUCTS__",
    ]


def test_runtime_clarifies_product_type_when_request_has_no_filters() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("r-1", day=1, product_type="A", config_version="1", system_no="SYS-1"),
        _record("r-2", day=2, product_type="B", config_version="1", system_no="SYS-2"),
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer([_report(actions=["task.nvh.record_search"], operations=[])]),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "show records")))

    assert response.plan.kind == "clarify"
    assert response.plan.missing_slots == ["product_type"]
    assert "page" not in response.plan.dataset.selection_params
    assert "rows" not in response.plan.dataset.selection_params
    assert [candidate.value for candidate in response.plan.prompts[0].candidates] == [
        "B",
        "A",
        "__ALL_PRODUCTS__",
    ]


def test_runtime_limits_product_type_candidates_with_latest_n() -> None:
    from maia.runtime import create_maia_runtime

    records = tuple(
        _record(
            f"r-{index}",
            day=index,
            product_type=f"P{index}",
            config_version="1",
            system_no=f"SYS-{index}",
        )
        for index in range(6, 0, -1)
    )
    record_client = _RecordClient(records)
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "latest_n", "target": "3"},
                    ],
                )
            ]
        ),
        record_client=record_client,
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "show latest 3 records")))

    assert response.plan.kind == "clarify"
    assert [candidate.value for candidate in response.plan.prompts[0].candidates] == [
        "P6",
        "P5",
        "P4",
        "__ALL_PRODUCTS__",
    ]
    assert record_client.calls[0] == (1, 3)


def test_runtime_keeps_product_type_when_record_scope_changes() -> None:
    from maia.runtime import ConversationStateRepository, create_maia_runtime

    records = (
        _record("r-1", day=1, product_type="OLD", config_version="0", system_no="SYS-OLD", summary_result="OLD_SUM"),
        _record("r-5", day=5, product_type="OLD", config_version="1", system_no="SYS-1", summary_result="PASS"),
        _record("r-6", day=6, product_type="OLD", config_version="2", system_no="SYS-2", summary_result="PASS"),
    )
    state_repository = ConversationStateRepository()
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "OLD"},
                        {"action": "replace", "entity_type": "config_version", "target": "0"},
                        {"action": "replace", "entity_type": "type_system", "target": "SYS-OLD"},
                        {"action": "replace", "entity_type": "summary_result", "target": "OLD_SUM"},
                        {"action": "replace", "entity_type": "latest_n", "target": "7"},
                    ],
                ),
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "summary_result", "target": "PASS"},
                    ],
                ),
            ]
        ),
        record_client=_RecordClient(records),
        state_repository=state_repository,
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        source_version="sigma-fixture-v1",
    )

    first = asyncio.run(handler.handle_turn(_request("s1", "find old records")))
    second = asyncio.run(handler.handle_turn(_request("s1", "find pass records")))
    draft = state_repository.load("s1").pending_selection_draft

    assert first.plan.kind == "task"
    assert second.plan.kind == "clarify"
    assert second.plan.prompts[0].id == "config_version"
    assert [candidate.value for candidate in second.plan.prompts[0].candidates] == ["2", "1"]
    assert draft is not None
    assert draft.limit == 7
    assert _predicate_values(draft.expression, "summary_result_in") == ("PASS",)
    assert _predicate_values(draft.expression, "product_type_in") == ("OLD",)
    assert _predicate_values(draft.expression, "config_version_in") == ()
    assert _predicate_values(draft.expression, "type_system_in") == ()


def test_runtime_clears_product_type_for_all_products_scope_request() -> None:
    from maia.runtime import ConversationStateRepository, create_maia_runtime

    selection_repository = InMemorySelectionSetRepository()
    records = (
        _record("r-1", day=1, product_type="OLD", config_version="0", system_no="SYS-OLD", summary_result="OLD_SUM"),
        _record("r-5", day=5, product_type="P2", config_version="1", system_no="SYS-2", summary_result="PASS"),
        _record("r-6", day=6, product_type="P3", config_version="1", system_no="SYS-3", summary_result="PASS"),
    )
    state_repository = ConversationStateRepository()
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "OLD"},
                        {"action": "replace", "entity_type": "config_version", "target": "0"},
                        {"action": "replace", "entity_type": "type_system", "target": "SYS-OLD"},
                        {"action": "replace", "entity_type": "summary_result", "target": "OLD_SUM"},
                    ],
                ),
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "summary_result", "target": "PASS"},
                    ],
                ),
            ]
        ),
        record_client=_RecordClient(records),
        selection_repository=selection_repository,
        state_repository=state_repository,
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        source_version="sigma-fixture-v1",
    )

    first = asyncio.run(handler.handle_turn(_request("s1", "find old records")))
    second = asyncio.run(handler.handle_turn(_request("s1", "find all product pass records 所有产品")))
    selection = selection_repository.get(second.plan.dataset.selection_set_id)

    assert first.plan.kind == "task"
    assert second.plan.kind == "task"
    assert second.plan.dataset.record_ids == ["r-5", "r-6"]
    assert selection is not None
    assert _predicate_values(selection.expression, "summary_result_in") == ("PASS",)
    assert _predicate_values(selection.expression, "product_type_in") == ()
    assert _predicate_values(selection.expression, "config_version_in") == ()
    assert _predicate_values(selection.expression, "type_system_in") == ()


def test_runtime_keeps_explicit_product_type_when_record_scope_changes() -> None:
    from maia.runtime import ConversationStateRepository, create_maia_runtime

    records = (
        _record("r-1", day=1, product_type="OLD", config_version="0", system_no="SYS-OLD", summary_result="OLD_SUM"),
        _record("r-5", day=5, product_type="NEW", config_version="1", system_no="SYS-1", summary_result="PASS"),
        _record("r-6", day=6, product_type="NEW", config_version="2", system_no="SYS-2", summary_result="PASS"),
    )
    state_repository = ConversationStateRepository()
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "OLD"},
                        {"action": "replace", "entity_type": "config_version", "target": "0"},
                        {"action": "replace", "entity_type": "type_system", "target": "SYS-OLD"},
                        {"action": "replace", "entity_type": "summary_result", "target": "OLD_SUM"},
                    ],
                ),
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "summary_result", "target": "PASS"},
                        {"action": "replace", "entity_type": "product_type", "target": "NEW"},
                    ],
                ),
            ]
        ),
        record_client=_RecordClient(records),
        state_repository=state_repository,
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        source_version="sigma-fixture-v1",
    )

    first = asyncio.run(handler.handle_turn(_request("s1", "find old records")))
    second = asyncio.run(handler.handle_turn(_request("s1", "find new pass records")))
    draft = state_repository.load("s1").pending_selection_draft

    assert first.plan.kind == "task"
    assert second.plan.kind == "clarify"
    assert second.plan.prompts[0].id == "config_version"
    assert draft is not None
    assert _predicate_values(draft.expression, "summary_result_in") == ("PASS",)
    assert _predicate_values(draft.expression, "product_type_in") == ("NEW",)
    assert _predicate_values(draft.expression, "config_version_in") == ()
    assert _predicate_values(draft.expression, "type_system_in") == ()


def test_runtime_latest_n_change_recomputes_product_candidates_from_latest_scope() -> None:
    from maia.runtime import ConversationStateRepository, create_maia_runtime

    records = tuple(
        (
            _record("r-6", day=6, product_type="P6", config_version="2", system_no="SYS-6"),
            _record("r-5", day=5, product_type="P6", config_version="1", system_no="SYS-5"),
            _record("r-4", day=4, product_type="P5", config_version="1", system_no="SYS-4"),
            _record("r-3", day=3, product_type="P4", config_version="1", system_no="SYS-3"),
        )
    )
    state_repository = ConversationStateRepository()
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "P6"},
                        {"action": "replace", "entity_type": "config_version", "target": "2"},
                        {"action": "replace", "entity_type": "type_system", "target": "SYS-6"},
                    ],
                ),
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "latest_n", "target": "3"},
                    ],
                ),
            ]
        ),
        record_client=_RecordClient(records),
        state_repository=state_repository,
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        source_version="sigma-fixture-v1",
    )

    first = asyncio.run(handler.handle_turn(_request("s1", "find P6 version 2 records")))
    second = asyncio.run(handler.handle_turn(_request("s1", "find latest 3 records")))
    draft = state_repository.load("s1").pending_selection_draft

    assert first.plan.kind == "task"
    assert second.plan.kind == "clarify"
    assert second.plan.prompts[0].id == "config_version"
    assert [candidate.value for candidate in second.plan.prompts[0].candidates] == ["2", "1"]
    assert draft is not None
    assert draft.limit == 3
    assert _predicate_values(draft.expression, "product_type_in") == ("P6",)
    assert _predicate_values(draft.expression, "config_version_in") == ()
    assert _predicate_values(draft.expression, "type_system_in") == ()


def test_runtime_limits_missing_product_type_candidates_to_recent_top_five_plus_all() -> None:
    from maia.runtime import create_maia_runtime

    records = tuple(
        _record(
            f"r-{index}",
            day=index,
            product_type=f"P{index}",
            config_version="1",
            system_no=f"SYS-{index}",
            summary_result="FAIL",
        )
        for index in range(1, 7)
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "summary_result", "target": "FAIL"},
                    ],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "show failing records")))

    assert response.plan.kind == "clarify"
    assert response.plan.message == "\u5f53\u524d\u7b5b\u9009\u7684\u6d4b\u8bd5\u8bb0\u5f55\u6309\u6d4b\u8bd5\u65f6\u95f4\u5012\u5e8f\u6db5\u76d6\u4e86P6\u3001P5\u3001P4\u7b49 6 \u4e2a\u4ea7\u54c1\u578b\u53f7\uff0c\u8bf7\u9009\u62e9\u4f60\u8981\u89c2\u5bdf\u7684\u4ea7\u54c1\u578b\u53f7\u3002"
    assert [candidate.value for candidate in response.plan.prompts[0].candidates] == [
        "P6",
        "P5",
        "P4",
        "P3",
        "P2",
        "__ALL_PRODUCTS__",
    ]


def test_runtime_invalid_product_type_candidates_follow_filtered_test_time_order() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("r-2", day=2, product_type="C", config_version="1", system_no="SYS-C", summary_result="FAIL"),
        _record("r-5", day=5, product_type="B", config_version="1", system_no="SYS-B", summary_result="FAIL"),
        _record("r-3", day=3, product_type="A", config_version="1", system_no="SYS-A", summary_result="FAIL"),
        _record("r-4", day=4, product_type="B", config_version="1", system_no="SYS-B", summary_result="FAIL"),
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "unknown"},
                        {"action": "replace", "entity_type": "summary_result", "target": "FAIL"},
                    ],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "find unknown failing records")))

    assert response.plan.kind == "clarify"
    assert response.plan.reason == "invalid_slots"
    assert response.plan.invalid_slots == ["product_type"]
    assert "按测试时间倒序" in response.plan.message
    assert [candidate.value for candidate in response.plan.prompts[0].candidates] == [
        "B",
        "A",
        "C",
        "__ALL_PRODUCTS__",
    ]


def test_runtime_all_product_apply_continues_pending_search_without_type_filter() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("r-1", day=1, product_type="A", config_version="1", system_no="SYS-1"),
        _record("r-2", day=2, product_type="A", config_version="1", system_no="SYS-1", summary_result="FAIL"),
        _record("r-4", day=4, product_type="B", config_version="1", system_no="SYS-2", summary_result="FAIL"),
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "summary_result", "target": "FAIL"},
                    ],
                ),
                _report(actions=["task.nvh.record_search"], operations=[]),
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        source_version="sigma-fixture-v1",
    )

    first = asyncio.run(handler.handle_turn(_request("s1", "show failing records")))
    second = asyncio.run(
        handler.handle_turn(
            _request(
                "s1",
                "",
                prompt_replies=[{"prompt_id": "product_type", "value": "__ALL_PRODUCTS__"}],
            )
        )
    )

    assert first.plan.kind == "clarify"
    assert second.plan.kind == "task"
    assert second.plan.dataset.record_ids == ["r-2", "r-4"]


def test_runtime_applies_product_type_reply_from_pending_prompt() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("r-1", day=1, product_type="hzzx", config_version="1", system_no="SYS-1", summary_result="FAIL"),
        _record("r-2", day=2, product_type="byd0601", config_version="1", system_no="SYS-2", summary_result="FAIL"),
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "summary_result", "target": "FAIL"},
                    ],
                ),
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {
                            "action": "replace",
                            "entity_type": "serial_number",
                            "target": "byd0601",
                            "slot_valid": False,
                        },
                    ],
                ),
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        source_version="sigma-fixture-v1",
    )

    first = asyncio.run(handler.handle_turn(_request("s1", "show failing records")))
    second = asyncio.run(
        handler.handle_turn(
            _request(
                "s1",
                "",
                prompt_replies=[{"prompt_id": "product_type", "value": "byd0601"}],
            )
        )
    )

    assert first.plan.kind == "clarify"
    assert first.plan.missing_slots == ["product_type"]
    assert second.plan.kind == "task"
    assert second.plan.dataset.record_ids == ["r-2"]


def test_runtime_applies_product_type_reply_outside_top_five_candidates() -> None:
    from maia.runtime import create_maia_runtime

    records = tuple(
        _record(
            f"r-{index}",
            day=index,
            product_type=f"P{index}",
            config_version="1",
            system_no=f"SYS-{index}",
            summary_result="FAIL",
        )
        for index in range(1, 7)
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "summary_result", "target": "FAIL"},
                    ],
                ),
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {
                            "action": "replace",
                            "entity_type": "serial_number",
                            "target": "P1",
                            "slot_valid": False,
                        },
                    ],
                ),
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        source_version="sigma-fixture-v1",
    )

    first = asyncio.run(handler.handle_turn(_request("s1", "show failing records")))
    second = asyncio.run(
        handler.handle_turn(
            _request(
                "s1",
                "",
                prompt_replies=[{"prompt_id": "product_type", "value": "P1"}],
            )
        )
    )

    assert first.plan.kind == "clarify"
    assert [candidate.value for candidate in first.plan.prompts[0].candidates] == [
        "P6",
        "P5",
        "P4",
        "P3",
        "P2",
        "__ALL_PRODUCTS__",
    ]
    assert second.plan.kind == "task"
    assert second.plan.dataset.record_ids == ["r-1"]


def test_runtime_applies_explicit_prompt_reply_without_recognizer_round_trip() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("r-1", day=1, product_type="A", config_version="1", system_no="SYS-1", summary_result="FAIL"),
        _record("r-2", day=2, product_type="B", config_version="1", system_no="SYS-2", summary_result="FAIL"),
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "summary_result", "target": "FAIL"},
                    ],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        source_version="sigma-fixture-v1",
    )

    first = asyncio.run(handler.handle_turn(_request("s1", "show failing records")))
    second = asyncio.run(
        handler.handle_turn(
            _request(
                "s1",
                "",
                prompt_replies=[{"prompt_id": "product_type", "value": "B"}],
            )
        )
    )

    assert first.plan.kind == "clarify"
    assert first.plan.prompts[0].id == "product_type"
    assert second.plan.kind == "task"
    assert second.plan.dataset.record_ids == ["r-2"]


def test_runtime_rejects_explicit_reply_for_non_pending_prompt() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("r-1", day=1, product_type="A", config_version="1", system_no="SYS-1", summary_result="FAIL"),
        _record("r-2", day=2, product_type="B", config_version="1", system_no="SYS-2", summary_result="FAIL"),
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "summary_result", "target": "FAIL"},
                    ],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        source_version="sigma-fixture-v1",
    )

    first = asyncio.run(handler.handle_turn(_request("s1", "show failing records")))
    second = asyncio.run(
        handler.handle_turn(
            _request(
                "s1",
                "",
                prompt_replies=[{"prompt_id": "config_version", "value": "1"}],
            )
        )
    )

    assert first.plan.kind == "clarify"
    assert first.plan.prompts[0].id == "product_type"
    assert second.plan.kind == "clarify"
    assert second.plan.reason == "ambiguous_slots"
    assert second.plan.message == "prompt reply is not pending: config_version"


def test_runtime_clarifies_config_version_and_apply_continues() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("r-1", day=1, product_type="A", config_version="1", system_no="SYS-1"),
        _record("r-3", day=3, product_type="A", config_version="2", system_no="SYS-2"),
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "A"},
                    ],
                ),
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "config_version", "target": "2"},
                    ],
                ),
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        source_version="sigma-fixture-v1",
    )

    first = asyncio.run(handler.handle_turn(_request("s1", "find A records")))
    second = asyncio.run(
        handler.handle_turn(
            _request(
                "s1",
                "",
                prompt_replies=[{"prompt_id": "config_version", "value": "2"}],
            )
        )
    )

    assert first.plan.kind == "clarify"
    assert first.plan.missing_slots == ["config_version"]
    assert first.plan.message == "当前已选择产品型号 A，请选择配置序号。"
    assert first.plan.prompts[0].message == "为产品型号 A 选择配置序号。"
    assert first.plan.prompts[0].input_type == "multi_select"
    assert [candidate.value for candidate in first.plan.prompts[0].candidates] == ["2", "1"]
    assert second.plan.kind == "task"
    assert second.plan.dataset.record_ids == ["r-3"]


def test_runtime_applies_config_version_reply_from_pending_prompt() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("r-1", day=1, product_type="A", config_version="1", system_no="SYS-1"),
        _record("r-3", day=3, product_type="A", config_version="2", system_no="SYS-2"),
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "A"},
                    ],
                ),
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {
                            "action": "replace",
                            "entity_type": "serial_number",
                            "target": "2",
                            "slot_valid": False,
                        },
                    ],
                ),
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        source_version="sigma-fixture-v1",
    )

    first = asyncio.run(handler.handle_turn(_request("s1", "find A records")))
    second = asyncio.run(
        handler.handle_turn(
            _request(
                "s1",
                "",
                prompt_replies=[{"prompt_id": "config_version", "value": "2"}],
            )
        )
    )

    assert first.plan.kind == "clarify"
    assert first.plan.missing_slots == ["config_version"]
    assert second.plan.kind == "task"
    assert second.plan.dataset.record_ids == ["r-3"]


def test_runtime_applies_multi_config_versions_and_multi_systems() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("r-1", day=1, product_type="A", config_version="1", system_no="SYS-1"),
        _record("r-2", day=2, product_type="A", config_version="2", system_no="SYS-2"),
        _record("r-3", day=3, product_type="B", config_version="2", system_no="SYS-9"),
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "A"},
                    ],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        source_version="sigma-fixture-v1",
    )

    first = asyncio.run(handler.handle_turn(_request("s1", "find A records")))
    second = asyncio.run(
        handler.handle_turn(
            _request(
                "s1",
                "",
                prompt_replies=[{"prompt_id": "config_version", "value": ["2", "1"]}],
            )
        )
    )
    third = asyncio.run(
        handler.handle_turn(
            _request(
                "s1",
                "",
                prompt_replies=[{"prompt_id": "type_system", "value": ["SYS-2", "SYS-1"]}],
            )
        )
    )

    assert first.plan.kind == "clarify"
    assert first.plan.prompts[0].id == "config_version"
    assert first.plan.prompts[0].input_type == "multi_select"
    assert second.plan.kind == "clarify"
    assert second.plan.message == "当前已选择产品型号 A、配置序号 2、1，请选择检测系统。"
    assert second.plan.prompts[0].id == "type_system"
    assert second.plan.prompts[0].message == "为产品型号 A、配置序号 2、1 选择检测系统。"
    assert second.plan.prompts[0].input_type == "multi_select"
    assert [candidate.value for candidate in second.plan.prompts[0].candidates] == [
        "SYS-2",
        "SYS-1",
    ]
    assert third.plan.kind == "task"
    assert third.plan.dataset.record_ids == ["r-1", "r-2"]


def test_runtime_clarifies_type_system_and_apply_continues() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("r-6", day=6, product_type="A", config_version="2", system_no="SYS-05"),
        _record("r-7", day=7, product_type="A", config_version="2", system_no="SYS-04"),
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "A"},
                        {"action": "replace", "entity_type": "config_version", "target": "2"},
                    ],
                ),
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "type_system", "target": "SYS-05"},
                    ],
                ),
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        source_version="sigma-fixture-v1",
    )

    first = asyncio.run(handler.handle_turn(_request("s1", "find A version 2 records")))
    second = asyncio.run(
        handler.handle_turn(
            _request(
                "s1",
                "",
                prompt_replies=[{"prompt_id": "type_system", "value": "SYS-05"}],
            )
        )
    )

    assert first.plan.kind == "clarify"
    assert first.plan.missing_slots == ["type_system"]
    assert first.plan.message == "当前已选择产品型号 A、配置序号 2，请选择检测系统。"
    assert first.plan.prompts[0].message == "为产品型号 A、配置序号 2 选择检测系统。"
    assert first.plan.prompts[0].input_type == "multi_select"
    assert [candidate.value for candidate in first.plan.prompts[0].candidates] == [
        "SYS-04",
        "SYS-05",
    ]
    assert second.plan.kind == "task"
    assert second.plan.dataset.record_ids == ["r-6"]


def test_runtime_marks_invalid_config_version_against_filtered_product_scope() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("r-1", day=1, product_type="A", config_version="1", system_no="SYS-1"),
        _record("r-3", day=3, product_type="A", config_version="2", system_no="SYS-2"),
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "A"},
                        {"action": "replace", "entity_type": "config_version", "target": "9"},
                    ],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "find A version 9 records")))

    assert response.plan.kind == "clarify"
    assert response.plan.reason == "invalid_slots"
    assert response.plan.invalid_slots == ["config_version"]
    assert [candidate.value for candidate in response.plan.prompts[0].candidates] == ["2", "1"]


def test_runtime_accepts_zero_version_from_filtered_records() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("r-5", day=5, product_type="HZXJ0515", config_version="0", system_no="SYS-1"),
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "HZXJ0515"},
                        {"action": "replace", "entity_type": "config_version", "target": "0"},
                    ],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "find HZXJ0515 version 0 records")))

    assert response.plan.kind == "task"
    assert response.plan.dataset.record_ids == ["r-5"]


class _SequenceRecognizer:
    def __init__(self, reports: list[RecognitionReport]) -> None:
        self._reports = iter(reports)

    async def recognize(
        self,
        message: str,
        *,
        resolver=None,
        include_diagnostics: bool = False,
    ) -> RecognitionReport:
        del message, resolver, include_diagnostics
        return next(self._reports)


class _SequenceThemisRecognizer:
    def __init__(self, decisions: list[IntentDecision]) -> None:
        self._decisions = iter(decisions)

    async def recognize(
        self,
        message: str,
        *,
        resolver=None,
    ) -> IntentDecision:
        del message, resolver
        return next(self._decisions)


class _RecordClient:
    def __init__(self, records: tuple[TestRecordSummary, ...]) -> None:
        self._records = records
        self.calls: list[tuple[int | None, int | None]] = []

    async def list_records(
        self,
        expression,
        *,
        workspace_context: WorkspaceContext | None,
        page: int | None = None,
        rows: int | None = None,
    ) -> TestRecordPage:
        del workspace_context
        self.calls.append((page, rows))
        filtered = tuple(record for record in self._records if _matches(record, expression))
        page_number = page or 1
        row_count = rows or 500
        start = (page_number - 1) * row_count
        end = start + row_count
        return TestRecordPage(total=len(filtered), records=filtered[start:end])


class _ProductCatalog:
    def __init__(self, configs: tuple[ProductConfig, ...]) -> None:
        self._configs = configs

    async def list_configs(self, *, lang: str = "zh") -> tuple[ProductConfig, ...]:
        del lang
        return self._configs


class _Materializer:
    def __init__(self) -> None:
        self._counter = 0
        self.calls: list[tuple[str | None, str | None, tuple[str, ...]]] = []

    async def materialize(
        self,
        selection_set,
        *,
        records=(),
        workspace_context,
        dataset_id=None,
        dataset_name=None,
    ) -> str:
        del selection_set, workspace_context
        self.calls.append(
            (dataset_id, dataset_name, tuple(record.record_id for record in records))
        )
        if dataset_id is not None:
            return dataset_id
        self._counter += 1
        return f"dataset-{self._counter}"


def _configs_from_records(
    records: tuple[TestRecordSummary, ...],
) -> tuple[ProductConfig, ...]:
    from maia.integrations.sigma.product_catalog import ProductConfig

    configs: list[ProductConfig] = []
    seen: set[tuple[str, str, str]] = set()
    for record in sorted(records, key=lambda item: item.tested_at or datetime(1970, 1, 1, tzinfo=UTC), reverse=True):
        key = (
            record.product_type or "",
            record.config_version or "",
            record.system_no or "",
        )
        if "" in key or key in seen:
            continue
        seen.add(key)
        configs.append(
            ProductConfig(
                product_type=key[0],
                config_version=key[1],
                type_system=key[2],
                update_time=record.tested_at,
            )
        )
    return tuple(configs)


def _record(
    record_id: str,
    *,
    day: int,
    product_type: str,
    config_version: str,
    system_no: str,
    summary_result: str = "PASS",
) -> TestRecordSummary:
    return TestRecordSummary(
        record_id=record_id,
        tested_at=datetime(2026, 6, day, 9, 30, tzinfo=UTC),
        product_type=product_type,
        config_version=config_version,
        system_no=system_no,
        serial_number=f"SN-{record_id}",
        summary_result=summary_result,
        available_artifacts=("raw_data",),
    )


def _matches(record: TestRecordSummary, expression) -> bool:
    if expression is None:
        return True
    parsed = parse_filter_expression(expression)
    if isinstance(parsed, Predicate):
        return _matches_predicate(record, parsed)
    if isinstance(parsed, AllOf):
        return all(_matches(record, child) for child in parsed.expressions)
    if isinstance(parsed, AnyOf):
        return any(_matches(record, child) for child in parsed.expressions)
    if isinstance(parsed, Not):
        return not _matches(record, parsed.expression)
    raise AssertionError(f"unsupported expression: {parsed}")


def _matches_predicate(record: TestRecordSummary, predicate: Predicate) -> bool:
    if predicate.name == "product_type_in":
        return record.product_type in _values(predicate)
    if predicate.name == "config_version_in":
        return record.config_version in _values(predicate)
    if predicate.name == "type_system_in":
        return record.system_no in _values(predicate)
    if predicate.name == "summary_result_in":
        return record.summary_result in _values(predicate)
    if predicate.name == "tested_at_between":
        start = predicate.params.get("start")
        end = predicate.params.get("end")
        tested_at = record.tested_at
        if tested_at is None:
            return False
        naive = tested_at.replace(tzinfo=None)
        if isinstance(start, str) and naive < datetime.strptime(start, "%Y-%m-%d %H:%M:%S"):
            return False
        if isinstance(end, str) and naive > datetime.strptime(end, "%Y-%m-%d %H:%M:%S"):
            return False
        return True
    raise AssertionError(f"unsupported predicate: {predicate.name}")


def _summary_result_values(expression) -> tuple[str, ...]:
    parsed = parse_filter_expression(expression)
    if isinstance(parsed, Predicate) and parsed.name == "summary_result_in":
        return _values(parsed)
    if isinstance(parsed, (AllOf, AnyOf)):
        for child in parsed.expressions:
            try:
                return _summary_result_values(child)
            except AssertionError:
                continue
    raise AssertionError(f"summary_result_in predicate not found: {parsed}")


def _predicate_values(expression, predicate_name: str) -> tuple[str, ...]:
    if expression is None:
        return ()
    parsed = parse_filter_expression(expression)
    if isinstance(parsed, Predicate):
        return _values(parsed) if parsed.name == predicate_name else ()
    if isinstance(parsed, (AllOf, AnyOf)):
        values: list[str] = []
        for child in parsed.expressions:
            values.extend(_predicate_values(child, predicate_name))
        return tuple(values)
    if isinstance(parsed, Not):
        return ()
    raise AssertionError(f"unsupported expression: {parsed}")


def _values(predicate: Predicate) -> tuple[str, ...]:
    raw = predicate.params.get("values")
    if isinstance(raw, tuple):
        return tuple(str(value) for value in raw)
    if raw is None:
        return ()
    return (str(raw),)


def _decision(action: str, target: str) -> IntentDecision:
    return IntentDecision(
        verdict=RecognitionVerdict.CLEAR,
        intents=(
            IntentMatch(
                name="task.nvh.selection.set_summary_result",
                score=0.95,
                slots=IntentSlot(
                    action=action,
                    entity_type="summary_result",
                    target=target,
                    slot_valid=True,
                ),
            ),
            IntentMatch(
                name="task.nvh.record_search",
                score=0.94,
                slots=IntentSlot(),
            ),
        ),
    )


def _report(
    *,
    actions: list[str],
    operations: list[dict[str, object]],
) -> RecognitionReport:
    return RecognitionReport(
        message="search",
        verdict="clear",
        requires_confirmation=False,
        degraded=False,
        action_intents=[{"name": name, "score": 0.95} for name in actions],
        slot_operations=[
            {
                "intent": "task.nvh.record_search",
                "score": 0.93,
                "slot_valid": True,
                **operation,
            }
            for operation in operations
        ],
    )


def _request(
    session_id: str,
    message: str,
    workspace_context: WorkspaceContext | None = None,
    prompt_replies: list[dict[str, object]] | None = None,
) -> TurnRequest:
    return TurnRequest(
        session_id=session_id,
        message=message,
        prompt_replies=[] if prompt_replies is None else prompt_replies,
        workspace_context=workspace_context,
    )
