from __future__ import annotations

import json
from pathlib import Path

import yaml


MANIFEST_PATH = Path("configs/maia/testdata/sigma_fixture_manifest.yaml")


def test_sigma_fixture_manifest_points_to_existing_assets() -> None:
    manifest = _load_yaml(MANIFEST_PATH)

    assert manifest["name"] == "MaiaSigmaFixtures"
    assert [fixture["id"] for fixture in manifest["fixtures"]] == ["offline_1152"]

    for fixture in manifest["fixtures"]:
        workspace_context_path = MANIFEST_PATH.parent / fixture["workspace_context_path"]
        snapshot_path = MANIFEST_PATH.parent / fixture["snapshot_path"]

        assert workspace_context_path.is_file()
        assert snapshot_path.is_file()


def test_sigma_fixture_workspace_context_and_snapshot_stay_in_sync() -> None:
    fixture = _load_yaml(MANIFEST_PATH)["fixtures"][0]
    workspace_context = _load_json(
        MANIFEST_PATH.parent / fixture["workspace_context_path"]
    )
    snapshot = _load_json(MANIFEST_PATH.parent / fixture["snapshot_path"])
    invariants = fixture["invariants"]

    assert workspace_context == {"lang": "zh"}
    assert workspace_context == snapshot["workspace_context"]
    assert snapshot["id"] == fixture["id"]
    assert sorted({row["domain"] for row in snapshot["rows"]}) == invariants["domains"]
    assert sorted({row["sensor"] for row in snapshot["rows"]}) == invariants["sensors"]
    assert sorted({row["test_segment"] for row in snapshot["rows"]}) == invariants[
        "test_segments"
    ]


def test_sigma_fixture_snapshot_preserves_shared_indicator_domain_example() -> None:
    fixture = _load_yaml(MANIFEST_PATH)["fixtures"][0]
    snapshot = _load_json(MANIFEST_PATH.parent / fixture["snapshot_path"])

    shared_domains: dict[str, list[str]] = {}
    for row in snapshot["rows"]:
        shared_domains.setdefault(row["indicator"], []).append(row["domain"])

    assert shared_domains["Order48"] == ["ONE_D", "TWO_D_OC"]
    assert {
        row["label"]
        for row in snapshot["rows"]
        if row["indicator"] == "Order48"
    } == {"one-d-48", "order-cut-48"}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))
