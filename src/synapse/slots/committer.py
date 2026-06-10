"""Atomic slot commit step."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from synapse.engine import TurnContext
from synapse.slots.contracts import SlotOperation
from synapse.slots.resolution import CLEAR_ALL_SLOT_REF, SLOT_OPERATIONS_ARTIFACT
from synapse.slots.state import SlotState, SlotStateDiff
from synapse.slots.validation import SLOT_VALIDATION_ARTIFACT, SlotValidationResult


SLOT_STATE_ARTIFACT = "slot_state"
SLOT_STATE_DIFF_ARTIFACT = "slot_state_diff"


@dataclass(frozen=True, slots=True)
class SlotCommitResult:
    state: SlotState
    diff: SlotStateDiff


class SlotPostCommitPolicy(Protocol):
    async def operations_for(
        self,
        *,
        state: SlotState,
        context: TurnContext,
        operations: tuple[SlotOperation, ...],
    ) -> tuple[SlotOperation, ...]:
        ...


class SlotCommitter:
    """Apply a validated operation batch as one committed state transition."""

    def commit(
        self,
        state: SlotState,
        operations: tuple,
    ) -> SlotCommitResult:
        updated = (
            SlotState()
            if _clears_all(operations)
            else state.apply_all(operations)
        )
        return SlotCommitResult(state=updated, diff=state.diff(updated))


class SlotCommitterStep:
    """Commit validated slot operations into the turn artifacts."""

    def __init__(
        self,
        slot_state: SlotState | None = None,
        committer: SlotCommitter | None = None,
        post_commit_policy: SlotPostCommitPolicy | None = None,
    ) -> None:
        self._initial_slot_state = slot_state or SlotState()
        self._states: dict[str, SlotState] = {}
        self._committer = committer or SlotCommitter()
        self._post_commit_policy = post_commit_policy

    async def run(self, context: TurnContext) -> TurnContext:
        validation = context.artifacts.get(SLOT_VALIDATION_ARTIFACT)
        if isinstance(validation, SlotValidationResult) and not validation.valid:
            raise ValueError("slot validation failed before commit")
        if validation is None:
            raise KeyError("missing slot validation artifact")

        operations = context.artifacts.get(SLOT_OPERATIONS_ARTIFACT, ())
        if not isinstance(operations, tuple):
            raise TypeError("slot operations artifact must be a tuple")

        state = self._state_for_session(context.request.session_id)
        result = self._committer.commit(state, operations)
        final_state = await self._apply_post_commit_policy(
            context,
            state=state,
            committed=result.state,
            diff=result.diff,
            operations=operations,
        )
        self._states[context.request.session_id] = final_state
        final_diff = state.diff(final_state)
        return (
            context.with_artifact(SLOT_STATE_ARTIFACT, final_state)
            .with_artifact(SLOT_STATE_DIFF_ARTIFACT, final_diff)
        )

    @property
    def states(self) -> Mapping[str, SlotState]:
        return dict(self._states)

    def _state_for_session(self, session_id: str) -> SlotState:
        return self._states.get(session_id, self._initial_slot_state)

    async def _apply_post_commit_policy(
        self,
        context: TurnContext,
        *,
        state: SlotState,
        committed: SlotState,
        diff: SlotStateDiff,
        operations: tuple[SlotOperation, ...],
    ) -> SlotState:
        if self._post_commit_policy is None:
            return committed
        committed_context = (
            context.with_artifact(SLOT_STATE_ARTIFACT, committed)
            .with_artifact(SLOT_STATE_DIFF_ARTIFACT, diff)
        )
        extra_operations = await self._post_commit_policy.operations_for(
            state=committed,
            context=committed_context,
            operations=operations,
        )
        if not extra_operations:
            return committed
        return self._committer.commit(committed, extra_operations).state


def _clears_all(operations: tuple) -> bool:
    return any(
        getattr(operation, "kind", None) == "clear"
        and getattr(operation, "ref", None) == CLEAR_ALL_SLOT_REF
        for operation in operations
    )
