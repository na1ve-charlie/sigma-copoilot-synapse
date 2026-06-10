from __future__ import annotations

from dataclasses import dataclass

from themis import IntentDecision, IntentMatch, IntentSlot

from synapse.engine import TurnContext
from synapse.planning.planner import DECISION_ARTIFACT


INDICATOR_LIST_INTENT = "task.nvh.data_observation.indicator_query.list"
INDICATORS_RESOLVER_QUERY_INTENT = "inquiry.nvh.resolver_query.indicators"
DATA_TYPE_ENTITY_TYPES = {"data_type", "data_types"}


@dataclass(frozen=True, slots=True)
class ObservationResolverQueryNormalizationStep:
    decision_artifact: str = DECISION_ARTIFACT

    async def run(self, context: TurnContext) -> TurnContext:
        decision = context.artifacts.get(self.decision_artifact)
        if not isinstance(decision, IntentDecision):
            return context
        if decision.action_intents:
            return context
        if not _should_inject_indicator_query(decision):
            return context

        injected = IntentMatch(
            name=INDICATORS_RESOLVER_QUERY_INTENT,
            score=_top_score(decision),
            slots=IntentSlot(),
        )
        normalized = IntentDecision(
            verdict=decision.verdict,
            intents=(*decision.intents, injected),
            diagnostics=decision.diagnostics,
            degraded=decision.degraded,
        )
        return context.with_artifact(self.decision_artifact, normalized)


def _should_inject_indicator_query(decision: IntentDecision) -> bool:
    for intent in decision.intents:
        if intent.name != INDICATOR_LIST_INTENT:
            continue
        if str(intent.slots.entity_type) not in DATA_TYPE_ENTITY_TYPES:
            continue
        if intent.slots.target:
            return True
    return False


def _top_score(decision: IntentDecision) -> float:
    return max((float(intent.score) for intent in decision.intents), default=1.0)
