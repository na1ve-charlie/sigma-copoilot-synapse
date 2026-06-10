"""Public /turns request and response contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TimeRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: str | None = None
    end: str | None = None


class TypeSystemContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    system_no: str

    def to_backend(self) -> dict[str, str]:
        return {"type": self.type, "systemNo": self.system_no}


class ProductContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_type: str
    product_version: str
    system_no: str


class WorkspaceContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_session_id: str | None = None
    data_load_mode: Literal["dataset", "filter"] | None = None
    dataset_id: str | None = None
    dataset_name: str | None = None
    dataset_origin: Literal["selected_dataset", "copilot_filter"] | None = None
    dataset_version: int | None = None
    filter_hash: str | None = None
    products: list[ProductContext] = Field(default_factory=list)
    test_time: TimeRange | None = None
    type_systems: list[TypeSystemContext] = Field(default_factory=list)
    lang: str = "zh"


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    message: str
    workspace_context: WorkspaceContext | None = None


class TurnResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: dict[str, Any]


TurnPlanResponse = TurnResponse


__all__ = [
    "ProductContext",
    "TimeRange",
    "TurnPlanResponse",
    "TurnRequest",
    "TurnResponse",
    "TypeSystemContext",
    "WorkspaceContext",
]
