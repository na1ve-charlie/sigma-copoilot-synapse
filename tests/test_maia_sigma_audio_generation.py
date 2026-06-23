from __future__ import annotations

import asyncio
import json

import pytest

from maia.api import WorkspaceContext


def test_ng_audio_generation_client_posts_record_id_array() -> None:
    from maia.integrations.sigma.audio_generation import (
        NgAudioGenerationClient,
        NgAudioGenerationRequest,
    )

    calls: list[tuple[str, dict[str, str], list[int]]] = []

    def transport(url: str, headers: dict[str, str], body: bytes | None, timeout: float):
        del timeout
        calls.append((url, headers, json.loads((body or b"[]").decode("utf-8"))))
        return 200, json.dumps({"code": 200, "data": None})

    client = NgAudioGenerationClient(
        base_url="http://sigma",
        token="token-1",
        transport=transport,
    )

    payload = asyncio.run(
        client.generate(
            NgAudioGenerationRequest(result_ids=(46467, 46478)),
            workspace_context=WorkspaceContext(lang="zh"),
        )
    )

    assert payload == {"code": 200, "data": None}
    assert calls == [
        (
            "http://sigma/api/storage/originData/ngAudionCreated?lang=zh",
            {"Content-Type": "application/json", "Token": "token-1"},
            [46467, 46478],
        )
    ]


def test_ng_audio_generation_client_maps_backend_error() -> None:
    from maia.integrations.sigma.audio_generation import (
        NgAudioGenerationClient,
        NgAudioGenerationError,
        NgAudioGenerationRequest,
    )

    def transport(url: str, headers: dict[str, str], body: bytes | None, timeout: float):
        del url, headers, body, timeout
        return 200, json.dumps({"code": 500, "msg": "failed"})

    client = NgAudioGenerationClient(base_url="http://sigma", transport=transport)

    with pytest.raises(NgAudioGenerationError, match="failed"):
        asyncio.run(
            client.generate(
                NgAudioGenerationRequest(result_ids=(46467,)),
                workspace_context=None,
            )
        )


def test_ng_audio_generation_request_requires_positive_ids() -> None:
    from maia.integrations.sigma.audio_generation import NgAudioGenerationRequest

    with pytest.raises(ValueError, match="resultIds must not be empty"):
        NgAudioGenerationRequest(result_ids=())
    with pytest.raises(ValueError, match="resultIds values must be positive"):
        NgAudioGenerationRequest(result_ids=(0,))
