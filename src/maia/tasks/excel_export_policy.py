from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from maia.api import ClarifyPlan, PlanDataset, Prompt, PromptCandidate, ReplyPlan, TaskPlan
from maia.conversation.draft import SelectionDraft
from maia.conversation.state import ConversationSelectionState
from maia.integrations.sigma.excel_export import ExcelExportError, ExcelExportRequest, SensorListError
from maia.integrations.sigma.records import TestRecordSummary
from maia.selection.sets import SelectionSet
from maia.tasks import PendingConfirmation, PendingTask, TaskSpec


EXCEL_EXPORT_INTENT = "task.nvh.excel_export"
SENSOR_ID_LIST_SLOT = "sensor_id_list"
EXCEL_DATA_TYPES_SLOT = "excel_data_types"
EXCEL_SCOPE_SLOT = "excel_scope"
PENDING_SELECTION_MARKER = f"__task__:{EXCEL_EXPORT_INTENT}"
CONFIRM_VALUES = {"\u786e\u8ba4", "\u662f", "\u597d\u7684", "\u6267\u884c", "confirm", "yes", "y"}
CANCEL_VALUES = {"\u53d6\u6d88", "\u4e0d\u7528", "\u4e0d\u8981", "cancel", "no", "n"}
DATA_TYPE_OPTIONS = (
    ("one_data", "\u4e00\u7ef4\u6570\u636e", "oneData"),
    ("two_data", "\u4e8c\u7ef4\u6570\u636e", "twoData"),
    ("result_data", "\u7ed3\u679c\u6570\u636e", "resultData"),
)
DATA_TYPE_VALUES = tuple(item[0] for item in DATA_TYPE_OPTIONS)


class ExcelExporter(Protocol):
    async def export(self, request: ExcelExportRequest, *, workspace_context) -> dict[str, object]: ...


class SensorLister(Protocol):
    async def list_sensors(
        self,
        *,
        type_: str,
        system_no: str,
        workspace_context,
    ) -> tuple[str, ...]: ...


class UnavailableExcelExporter:
    async def export(self, request: ExcelExportRequest, *, workspace_context) -> dict[str, object]:
        raise ExcelExportError("Excel export client is not configured.")


class UnavailableSensorLister:
    async def list_sensors(
        self,
        *,
        type_: str,
        system_no: str,
        workspace_context,
    ) -> tuple[str, ...]:
        raise SensorListError("Sensor list client is not configured.")


@dataclass(frozen=True)
class ExcelExportScope:
    product_type: str
    config_version: str
    system_no: str

    @property
    def type_(self) -> str:
        return f"{self.product_type}_{self.config_version}"

    @property
    def label(self) -> str:
        return f"{self.product_type} / {self.config_version} / {self.system_no}"

    def prompt_value(self) -> dict[str, str]:
        return {
            "product_type": self.product_type,
            "config_version": self.config_version,
            "system_no": self.system_no,
        }


class ExcelExportPolicy:
    def task_for_selection(
        self,
        selection: SelectionSet,
        *,
        params: dict[str, object],
        task_id: str,
    ) -> TaskSpec:
        return TaskSpec(
            task_id=task_id,
            name=EXCEL_EXPORT_INTENT,
            title="Excel export",
            operations=(EXCEL_EXPORT_INTENT,),
            selection_set_id=selection.selection_set_id,
            selection_hash=selection.selection_hash,
            params=params,
            risk_level="medium",
            requires_confirmation=True,
        )

    def reply(self, message: str) -> ReplyPlan:
        return ReplyPlan(message=message)

    def task_plan(self, task: TaskSpec | PendingTask, message: str) -> TaskPlan:
        return TaskPlan(
            status="ready",
            name=task.name,
            intent=EXCEL_EXPORT_INTENT,
            title=task.title,
            risk_level=task.risk_level,
            requires_confirmation=task.requires_confirmation,
            params=task.params,
            message=message,
        )

    def blocked_plan(self, task: TaskSpec | PendingTask, reason: str, message: str) -> TaskPlan:
        return TaskPlan(
            status="blocked",
            name=task.name,
            intent=EXCEL_EXPORT_INTENT,
            title=task.title,
            risk_level=task.risk_level,
            requires_confirmation=task.requires_confirmation,
            params=task.params,
            message=message,
            reason=reason,
        )

    def clarify_scope(
        self,
        *,
        task: TaskSpec | PendingTask,
        scopes: tuple[ExcelExportScope, ...],
        records: tuple[TestRecordSummary, ...],
        dataset: PlanDataset,
        invalid: bool = False,
    ) -> ClarifyPlan:
        counts = scope_counts(records)
        prompt = Prompt(
            id=EXCEL_SCOPE_SLOT,
            target="slot",
            label="excel export scope",
            message="Select one product/config/system for Excel export.",
            required=True,
            input_type="single_select",
            candidates=[
                PromptCandidate(
                    value=scope.prompt_value(),
                    label=scope.label,
                    description=f"{counts.get(scope, 0)} records",
                )
                for scope in scopes
            ],
        )
        slot_field = "invalid_slots" if invalid else "missing_slots"
        return ClarifyPlan(
            reason="invalid_slots" if invalid else "ambiguous_slots",
            message="Select one product/config/system for Excel export.",
            pending_task=EXCEL_EXPORT_INTENT,
            prompts=[prompt],
            suggestions=[scope.label for scope in scopes],
            dataset=dataset,
            **{slot_field: [EXCEL_SCOPE_SLOT]},
        )

    def clarify_sensors(
        self,
        *,
        sensors: tuple[str, ...],
        dataset: PlanDataset,
        invalid: bool = False,
    ) -> ClarifyPlan:
        prompt = Prompt(
            id=SENSOR_ID_LIST_SLOT,
            target="slot",
            label="sensors",
            message="Select sensors to export.",
            required=True,
            input_type="multi_select",
            candidates=[PromptCandidate(value=sensor, label=sensor) for sensor in sensors],
        )
        slot_field = "invalid_slots" if invalid else "missing_slots"
        return ClarifyPlan(
            reason="invalid_slots" if invalid else "missing_slots",
            message="Select sensors to export.",
            pending_task=EXCEL_EXPORT_INTENT,
            prompts=[prompt],
            suggestions=list(sensors),
            dataset=dataset,
            **{slot_field: [SENSOR_ID_LIST_SLOT]},
        )

    def clarify_data_types(self, *, dataset: PlanDataset, invalid: bool = False) -> ClarifyPlan:
        prompt = Prompt(
            id=EXCEL_DATA_TYPES_SLOT,
            target="slot",
            label="data types",
            message="Select Excel data types to export.",
            required=True,
            input_type="multi_select",
            candidates=[PromptCandidate(value=label, label=label) for _, label, _ in DATA_TYPE_OPTIONS],
        )
        labels = [label for _, label, _ in DATA_TYPE_OPTIONS]
        slot_field = "invalid_slots" if invalid else "missing_slots"
        return ClarifyPlan(
            reason="invalid_slots" if invalid else "missing_slots",
            message="Select Excel data types to export.",
            pending_task=EXCEL_EXPORT_INTENT,
            prompts=[prompt],
            suggestions=labels,
            dataset=dataset,
            **{slot_field: [EXCEL_DATA_TYPES_SLOT]},
        )


def is_excel_task(task: TaskSpec | PendingTask | None) -> bool:
    return task is not None and EXCEL_EXPORT_INTENT in task.operations


def is_excel_confirmation(confirmation: PendingConfirmation | None) -> bool:
    return confirmation is not None and EXCEL_EXPORT_INTENT in confirmation.task.operations


def is_pending_excel_selection(draft: SelectionDraft | None) -> bool:
    return draft is not None and PENDING_SELECTION_MARKER in draft.pending_questions


def mark_pending_excel_selection(state: ConversationSelectionState) -> ConversationSelectionState:
    draft = state.pending_selection_draft
    if draft is None or PENDING_SELECTION_MARKER in draft.pending_questions:
        return state
    return state.model_copy(
        update={
            "pending_selection_draft": draft.model_copy(
                update={"pending_questions": (*draft.pending_questions, PENDING_SELECTION_MARKER)}
            ),
            "version": state.version + 1,
        }
    )


def pending_task(task: TaskSpec | PendingTask, missing_slot: str) -> PendingTask:
    payload = task.model_dump(mode="python")
    payload.pop("missing_slots", None)
    return PendingTask(**payload, missing_slots=(missing_slot,))


def params_from_prompt_replies(replies: Iterable[object]) -> dict[str, object]:
    params: dict[str, object] = {}
    for reply in replies:
        prompt_id = getattr(reply, "prompt_id", None)
        if prompt_id == SENSOR_ID_LIST_SLOT:
            sensors = sensor_param(getattr(reply, "value", None))
            if sensors:
                params[SENSOR_ID_LIST_SLOT] = sensors
        elif prompt_id == EXCEL_DATA_TYPES_SLOT:
            data_types = data_types_param(getattr(reply, "value", None))
            if data_types:
                params[EXCEL_DATA_TYPES_SLOT] = data_types
        elif prompt_id == EXCEL_SCOPE_SLOT:
            params[EXCEL_SCOPE_SLOT] = getattr(reply, "value", None)
    return params


def initial_params_from_message(message: str) -> dict[str, object]:
    data_types = data_types_from_message(message)
    return {EXCEL_DATA_TYPES_SLOT: data_types} if data_types else {}


def sensor_param(value: object) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_text(item) for item in _items(value) if _text(item)))


def data_types_param(value: object) -> tuple[str, ...]:
    normalized: list[str] = []
    for item in _items(value):
        text = _text(item)
        if text is None:
            continue
        exact = _data_type_value(text)
        values = (exact,) if exact is not None else data_types_from_message(text)
        for candidate in values or ():
            if candidate not in normalized:
                normalized.append(candidate)
    return tuple(normalized)


def data_types_from_message(message: str) -> tuple[str, ...]:
    normalized = message.casefold()
    if any(marker in normalized for marker in ("\u5168\u90e8\u6570\u636e", "\u6240\u6709\u6570\u636e", "\u5168\u91cf\u6570\u636e", "all data")):
        return DATA_TYPE_VALUES
    matched: list[str] = []
    for value, _label, aliases in (
        ("one_data", "\u4e00\u7ef4\u6570\u636e", ("\u4e00\u7ef4", "1d", "one data")),
        ("two_data", "\u4e8c\u7ef4\u6570\u636e", ("\u4e8c\u7ef4", "2d", "two data")),
        ("result_data", "\u7ed3\u679c\u6570\u636e", ("\u7ed3\u679c\u6570\u636e", "\u7ed3\u679c", "result")),
    ):
        if any(alias in normalized for alias in aliases):
            matched.append(value)
    return tuple(dict.fromkeys(matched))


def sensors_from_message(message: str, candidates: tuple[str, ...]) -> tuple[str, ...]:
    normalized = message.casefold()
    if any(marker in normalized for marker in ("\u5168\u90e8\u4f20\u611f\u5668", "\u6240\u6709\u4f20\u611f\u5668", "all sensors")):
        return candidates
    return tuple(candidate for candidate in candidates if candidate.casefold() in normalized)


def scope_param(value: object, scopes: tuple[ExcelExportScope, ...]) -> ExcelExportScope | None:
    if isinstance(value, dict):
        product_type = _text(value.get("product_type"))
        config_version = _text(value.get("config_version"))
        system_no = _text(value.get("system_no"))
        if product_type and config_version and system_no:
            candidate = ExcelExportScope(product_type, config_version, system_no)
            return candidate if candidate in scopes else None
    text = _text(value)
    if text is None:
        return None
    return next(
        (
            scope
            for scope in scopes
            if text in {scope.label, scope.system_no, scope.type_, f"{scope.type_}/{scope.system_no}"}
        ),
        None,
    )


def scopes(records: tuple[TestRecordSummary, ...]) -> tuple[ExcelExportScope, ...]:
    return tuple(
        dict.fromkeys(
            ExcelExportScope(record.product_type, record.config_version, record.system_no)
            for record in records
            if record.product_type and record.config_version and record.system_no
        )
    )


def scope_counts(records: tuple[TestRecordSummary, ...]) -> Counter[ExcelExportScope]:
    return Counter(
        ExcelExportScope(record.product_type, record.config_version, record.system_no)
        for record in records
        if record.product_type and record.config_version and record.system_no
    )


def records_for_scope(
    records: tuple[TestRecordSummary, ...],
    scope: ExcelExportScope,
) -> tuple[TestRecordSummary, ...]:
    return tuple(
        record
        for record in records
        if record.product_type == scope.product_type
        and record.config_version == scope.config_version
        and record.system_no == scope.system_no
    )


def validate_sensors(
    value: object,
    candidates: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    by_key = {candidate.casefold(): candidate for candidate in candidates}
    selected: list[str] = []
    invalid: list[str] = []
    for item in sensor_param(value):
        candidate = by_key.get(item.casefold())
        if candidate is None:
            invalid.append(item)
        elif candidate not in selected:
            selected.append(candidate)
    return tuple(selected), tuple(invalid)


def data_flags(data_types: tuple[str, ...]) -> dict[str, int]:
    selected = set(data_types)
    return {
        backend_key: 1 if value in selected else 0
        for value, _label, backend_key in DATA_TYPE_OPTIONS
    }


def export_request(
    records: tuple[TestRecordSummary, ...],
    scope: ExcelExportScope,
    sensors: tuple[str, ...],
    data_types: tuple[str, ...],
) -> ExcelExportRequest:
    return ExcelExportRequest(
        type_=scope.type_,
        system_no=scope.system_no,
        id_list=tuple(record_id(record) for record in records),
        sensor_id_list=sensors,
        **_request_flags(data_types),
    )


def request_from_task(task: TaskSpec) -> ExcelExportRequest:
    return ExcelExportRequest(
        type_=str(task.params["type"]),
        system_no=str(task.params["systemNo"]),
        id_list=tuple(int(value) for value in task.params["idList"]),
        sensor_id_list=tuple(str(value) for value in task.params["sensorIdList"]),
        one_data=int(task.params["oneData"]),
        two_data=int(task.params["twoData"]),
        result_data=int(task.params["resultData"]),
    )


def record_id(record: TestRecordSummary) -> int:
    text = record.record_id.strip()
    if not text.isdigit():
        raise ValueError(f"record_id must be numeric for Excel export: {record.record_id}")
    return int(text)


def _request_flags(data_types: tuple[str, ...]) -> dict[str, int]:
    flags = data_flags(data_types)
    return {
        "one_data": flags["oneData"],
        "two_data": flags["twoData"],
        "result_data": flags["resultData"],
    }


def _data_type_value(text: str) -> str | None:
    normalized = text.strip().casefold()
    for value, label, backend_key in DATA_TYPE_OPTIONS:
        if normalized in {value, label.casefold(), backend_key.casefold()}:
            return value
    return None


def _items(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set)):
        return tuple(value)
    return (value,)


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "CANCEL_VALUES",
    "CONFIRM_VALUES",
    "EXCEL_DATA_TYPES_SLOT",
    "EXCEL_EXPORT_INTENT",
    "EXCEL_SCOPE_SLOT",
    "SENSOR_ID_LIST_SLOT",
    "ExcelExportPolicy",
    "ExcelExportScope",
    "ExcelExporter",
    "SensorLister",
    "UnavailableExcelExporter",
    "UnavailableSensorLister",
    "data_flags",
    "data_types_from_message",
    "data_types_param",
    "export_request",
    "initial_params_from_message",
    "is_excel_confirmation",
    "is_excel_task",
    "is_pending_excel_selection",
    "mark_pending_excel_selection",
    "params_from_prompt_replies",
    "pending_task",
    "records_for_scope",
    "request_from_task",
    "scope_param",
    "scopes",
    "sensors_from_message",
    "validate_sensors",
]
