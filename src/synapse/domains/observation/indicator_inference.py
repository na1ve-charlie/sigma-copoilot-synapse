from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from themis import IntentDecision, IntentMatch, IntentSlot

from synapse.domains.observation.pending_request import (
    CLEAR_PENDING_OBSERVATION_REQUEST_ARTIFACT,
    PENDING_OBSERVATION_REQUEST_ARTIFACT,
    PendingObservationRequest,
)
from synapse.domains.observation.scope import (
    ObservationScopeContext,
    ObservationScopeDecision,
    ObservationScopePolicy,
    build_observation_scope_policy,
)
from synapse.engine import TurnContext
from synapse.planning.display import slot_label, slot_prompt_message
from synapse.planning.planner import DECISION_ARTIFACT
from synapse.planning.plans import ClarifyPlan, Prompt, PromptCandidate
from synapse.recognition import CANDIDATE_CATALOG_ARTIFACT, CandidateCatalog, CandidateItem


_INDICATOR_ENTITY_TYPES = {"indicator", "indicator_name", "indicator_names"}
_DATA_TYPE_ENTITY_TYPES = {"data_type", "data_types"}
_RESOLVER_QUERY_PREFIX = "inquiry.nvh.resolver_query."


@dataclass(frozen=True, slots=True)
class ObservationIndicatorInferenceStep:
    task_intent_by_data_type: Mapping[str, str]
    data_types_by_task_intent: Mapping[str, Sequence[str]]
    decision_artifact: str = DECISION_ARTIFACT
    scope_policy: ObservationScopePolicy | None = None

    async def run(self, context: TurnContext) -> TurnContext:
        decision = context.artifacts.get(self.decision_artifact)
        catalog = context.artifacts.get(CANDIDATE_CATALOG_ARTIFACT)
        if not isinstance(decision, IntentDecision) or not isinstance(
            catalog,
            CandidateCatalog,
        ):
            return context

        result = _infer_indicator_decision(
            decision=decision,
            catalog=catalog,
            artifacts=context.artifacts,
            message=context.message,
            task_intent_by_data_type=self.task_intent_by_data_type,
            data_types_by_task_intent=self.data_types_by_task_intent,
            scope_policy=self.scope_policy
            or build_observation_scope_policy(self._data_types_for_task_intent),
        )
        if result is None:
            return context

        updated = context.with_artifact(self.decision_artifact, result.decision)
        if result.pending_request is not None:
            updated = updated.with_artifact(
                PENDING_OBSERVATION_REQUEST_ARTIFACT,
                result.pending_request,
            )
        if result.clear_pending_request:
            updated = updated.with_artifact(
                CLEAR_PENDING_OBSERVATION_REQUEST_ARTIFACT,
                True,
            )
        if result.plan is None:
            return updated
        return updated.with_plan(result.plan.model_dump(mode="json"))

    def _data_types_for_task_intent(self, intent_name: str) -> Sequence[str]:
        return self.data_types_by_task_intent.get(intent_name, ())


@dataclass(frozen=True, slots=True)
class _InferenceResult:
    decision: IntentDecision
    plan: ClarifyPlan | None = None
    pending_request: PendingObservationRequest | None = None
    clear_pending_request: bool = False


def _infer_indicator_decision(
    *,
    decision: IntentDecision,
    catalog: CandidateCatalog,
    artifacts: Mapping[str, object],
    message: str,
    task_intent_by_data_type: Mapping[str, str],
    data_types_by_task_intent: Mapping[str, Sequence[str]],
    scope_policy: ObservationScopePolicy,
) -> _InferenceResult | None:
    pending_request = artifacts.get(PENDING_OBSERVATION_REQUEST_ARTIFACT)
    restored_data_type = _pending_data_type_selection(
        decision,
        pending_request,
        catalog,
        message,
    )
    rewritten_intents: list[IntentMatch] = []
    explicit_data_types: list[str] = (
        [restored_data_type]
        if restored_data_type is not None
        else list(_pre_recognition_explicit_data_types(artifacts, catalog))
    )
    indicator_targets: list[str] = []
    changed = False

    for intent in decision.intents:
        slots = intent.slots
        if _is_actionless_data_type_scope(intent, data_types_by_task_intent):
            explicit_data_types.extend(_slot_targets(slots.target))
            rewritten_intents.append(
                IntentMatch(name=intent.name, score=intent.score, slots=IntentSlot())
            )
            changed = True
            continue

        if _has_explicit_data_type_slot(slots):
            explicit_data_types.extend(_slot_targets(slots.target))

        if _has_indicator_target(slots):
            if _is_resolver_query_intent(intent):
                rewritten_intents.append(intent)
                continue
            canonical_target = _canonical_indicator_target(str(slots.target), catalog)
            indicator_targets.extend(
                _slot_targets(canonical_target or str(slots.target))
            )
            if canonical_target is not None and (
                canonical_target != slots.target
                or not slots.action
                or slots.slot_valid is False
            ):
                rewritten_intents.append(
                    IntentMatch(
                        name=intent.name,
                        score=intent.score,
                        slots=IntentSlot(
                            action=slots.action or "replace",
                            entity_type="indicator",
                            target=canonical_target,
                            slot_valid=True,
                        ),
                    )
                )
                changed = True
                continue

        rewritten_intents.append(intent)

    if (
        restored_data_type is not None
        and isinstance(pending_request, PendingObservationRequest)
    ):
        rewritten_intents.append(
            IntentMatch(
                name=pending_request.intent_name,
                score=pending_request.score,
                slots=IntentSlot(
                    action="replace",
                    entity_type="indicator",
                    target=pending_request.indicator_name,
                    slot_valid=True,
                ),
            )
        )
        indicator_targets.append(pending_request.indicator_name)
        changed = True

    explicit_data_types.extend(
        data_type
        for intent in rewritten_intents
        if _is_action_intent(intent)
        for data_type in data_types_by_task_intent.get(intent.name, ())
    )

    if not indicator_targets:
        return (
            _InferenceResult(_rebuild_decision(decision, rewritten_intents))
            if changed
            else None
        )

    indicator_data_types = _indicator_data_types(catalog, indicator_targets)
    available_data_types = tuple(
        item.value for item in catalog.candidates_for_entity("data_types")
    )
    scope = scope_policy.resolve(
        ObservationScopeContext(
            explicit_data_types=tuple(explicit_data_types),
            indicator_data_types=indicator_data_types,
            available_data_types=available_data_types,
        )
    )

    inferred_intent = (
        task_intent_by_data_type.get(scope.data_type)
        if scope.status == "resolved" and scope.data_type is not None
        else None
    )
    if inferred_intent and inferred_intent not in {
        intent.name for intent in rewritten_intents if _is_action_intent(intent)
    }:
        score = max((intent.score for intent in decision.intents), default=1.0)
        rewritten_intents.append(
            IntentMatch(name=inferred_intent, score=score, slots=IntentSlot())
        )
        changed = True

    if scope.status in {"conflict", "invalid"}:
        request = (
            _pending_request(rewritten_intents, indicator_targets, scope.candidates)
            if scope.status == "conflict" and restored_data_type is None
            else None
        )
        return _InferenceResult(
            _rebuild_decision(decision, rewritten_intents),
            _clarify_plan(scope, catalog),
            pending_request=request,
        )

    if not changed:
        return None
    return _InferenceResult(
        _rebuild_decision(decision, rewritten_intents),
        clear_pending_request=restored_data_type is not None,
    )


def _pending_request(
    intents: Sequence[IntentMatch],
    indicator_targets: Sequence[str],
    candidate_data_types: Sequence[str],
) -> PendingObservationRequest | None:
    if len(indicator_targets) != 1:
        return None
    for intent in intents:
        if _is_resolver_query_intent(intent) or not _has_indicator_target(intent.slots):
            continue
        return PendingObservationRequest(
            indicator_name=indicator_targets[0],
            candidate_data_types=tuple(candidate_data_types),
            intent_name=intent.name,
            score=intent.score,
        )
    return None


def _pending_data_type_selection(
    decision: IntentDecision,
    pending_request: object,
    catalog: CandidateCatalog,
    message: str,
) -> str | None:
    if not isinstance(pending_request, PendingObservationRequest):
        return None
    if any(_has_indicator_target(intent.slots) for intent in decision.intents):
        return None
    selected = [
        value
        for intent in decision.intents
        if _has_explicit_data_type_slot(intent.slots)
        for target in _slot_targets(intent.slots.target)
        if (value := _canonical_data_type(target, catalog)) is not None
    ]
    if not selected:
        value = _canonical_data_type(message.strip(), catalog)
        if value is not None:
            selected.append(value)
    valid = tuple(
        dict.fromkeys(
            value
            for value in selected
            if value in pending_request.candidate_data_types
        )
    )
    return valid[0] if len(valid) == 1 else None


def _canonical_data_type(
    target: str,
    catalog: CandidateCatalog,
) -> str | None:
    folded = target.casefold()
    for item in catalog.candidates_for_entity("data_types"):
        aliases = [item.value, item.label, *_metadata_strings(item.metadata.get("aliases"))]
        if any(alias and folded == alias.casefold() for alias in aliases):
            return item.value
    return None


def _rebuild_decision(
    decision: IntentDecision,
    intents: Sequence[IntentMatch],
) -> IntentDecision:
    return IntentDecision(
        verdict=decision.verdict,
        intents=tuple(intents),
        diagnostics=decision.diagnostics,
        degraded=decision.degraded,
    )


def _clarify_plan(
    decision: ObservationScopeDecision,
    catalog: CandidateCatalog,
) -> ClarifyPlan:
    prompt = Prompt(
        id="data_types",
        target="slot",
        label=slot_label("data_types"),
        message=slot_prompt_message("data_types"),
        required=True,
        input_type="single_select",
        candidates=_prompt_candidates(catalog, decision.candidates),
    )
    if decision.status == "invalid":
        return ClarifyPlan(
            reason="invalid_slots",
            message="当前数据类型无效。",
            invalid_slots=["data_types"],
            prompts=[prompt],
        )
    return ClarifyPlan(
        reason="ambiguous_slots",
        message="当前数据类型存在歧义。",
        missing_slots=["data_types"],
        prompts=[prompt],
    )


def _prompt_candidates(
    catalog: CandidateCatalog,
    values: Sequence[str],
) -> list[PromptCandidate]:
    by_value = {
        item.value: item for item in catalog.candidates_for_entity("data_types")
    }
    result = []
    for value in values:
        item = by_value.get(value, CandidateItem(value=value))
        result.append(
            PromptCandidate(
                value=item.value,
                label=item.label or item.value,
            )
        )
    return result


def _indicator_data_types(
    catalog: CandidateCatalog,
    targets: Sequence[str],
) -> tuple[str, ...]:
    values: list[str] = []
    target_set = {str(target) for target in targets}
    for item in catalog.candidates_for_entity("indicator_names"):
        if item.value not in target_set and item.label not in target_set:
            continue
        values.extend(_metadata_strings(item.metadata.get("data_types")))
        values.extend(_metadata_strings(item.metadata.get("data_type")))
    return tuple(dict.fromkeys(values))


def _has_indicator_target(slots: IntentSlot) -> bool:
    return bool(slots.target) and slots.entity_type in _INDICATOR_ENTITY_TYPES


def _has_explicit_data_type_slot(slots: IntentSlot) -> bool:
    return bool(slots.target) and slots.entity_type in _DATA_TYPE_ENTITY_TYPES


def _is_actionless_data_type_scope(
    intent: IntentMatch,
    data_types_by_task_intent: Mapping[str, Sequence[str]],
) -> bool:
    return (
        intent.name in data_types_by_task_intent
        and not intent.slots.action
        and _has_explicit_data_type_slot(intent.slots)
    )


def _is_action_intent(intent: IntentMatch) -> bool:
    slots = intent.slots
    return not (slots.action or slots.entity_type or slots.target)


def _is_resolver_query_intent(intent: IntentMatch) -> bool:
    return intent.name.startswith(_RESOLVER_QUERY_PREFIX)


def _slot_targets(target: str) -> tuple[str, ...]:
    return (str(target),) if target else ()


def _metadata_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [str(item) for item in value if item]
    return []


def _pre_recognition_explicit_data_types(
    artifacts: Mapping[str, object],
    catalog: CandidateCatalog,
) -> tuple[str, ...]:
    result = artifacts.get("pre_recognition")
    effects = getattr(result, "effects", ())
    for effect in effects or ():
        diagnostics = getattr(effect, "diagnostics", {})
        if not isinstance(diagnostics, Mapping):
            continue
        matches = diagnostics.get("observation_matches")
        if isinstance(matches, Sequence) and any(
            item in {"data_type", "data_types"} for item in matches
        ):
            return tuple(
                item.value for item in catalog.candidates_for_entity("data_types")
            )
    return ()


def _canonical_indicator_target(
    target: str,
    catalog: CandidateCatalog,
) -> str | None:
    base = _strip_parenthesized_suffix(target)
    for candidate in catalog.candidates_for_entity("indicator_names"):
        if _same_text(target, candidate.value) or _same_text(target, candidate.label):
            return candidate.value
        if _same_text(base, candidate.value) or _same_text(base, candidate.label):
            return candidate.value
    return None


def _strip_parenthesized_suffix(value: str) -> str:
    text = value.strip()
    for marker in (" (", "（"):
        index = text.find(marker)
        if index > 0:
            return text[:index].strip()
    return text


def _same_text(left: str, right: str | None) -> bool:
    return right is not None and right != "" and left.casefold() == right.casefold()
