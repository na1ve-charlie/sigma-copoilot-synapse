from __future__ import annotations

from typing import Protocol

from synapse.planning.planner import PlanningContext
from synapse.planning.plans import Plan


class ResolverQueryHandler(Protocol):
    """Build a plan for resolver-query intents when one applies."""

    async def build(
        self,
        context: PlanningContext,
        intent_names: tuple[str, ...],
    ) -> Plan | None:
        ...
