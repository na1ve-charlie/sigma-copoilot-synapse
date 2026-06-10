from __future__ import annotations

from pathlib import Path

import yaml


def test_calibration_cases_cover_multi_resolver_query_intents() -> None:
    payload = yaml.safe_load(
        Path("configs/themis/calibration_cases.yaml").read_text(encoding="utf-8")
    )
    cases = {case["message"]: case for case in payload["cases"]}

    assert cases["有哪些传感器"]["expected"] == "inquiry.nvh.resolver_query.sensors"
    assert cases["有哪些指标"]["expected"] == "inquiry.nvh.resolver_query.indicators"
    assert cases["有哪些传感器、测试段"]["expected"] == [
        "inquiry.nvh.resolver_query.sensors",
        "inquiry.nvh.resolver_query.test_segments",
    ]
    assert cases["有哪些传感器、测试段、指标"]["expected"] == [
        "inquiry.nvh.resolver_query.sensors",
        "inquiry.nvh.resolver_query.test_segments",
        "inquiry.nvh.resolver_query.indicators",
    ]
