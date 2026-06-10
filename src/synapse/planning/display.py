from __future__ import annotations


_SLOT_LABELS = {
    "data_type": "数据类型",
    "data_types": "数据类型",
    "sensor": "传感器",
    "sensors": "传感器",
    "test_segment": "测试段",
    "test_segments": "测试段",
    "indicator": "指标名",
    "indicator_name": "指标名",
    "indicator_names": "指标名",
}


def slot_label(slot_name: str) -> str:
    return _SLOT_LABELS.get(slot_name, slot_name)


def slot_prompt_message(slot_name: str) -> str:
    return f"请选择{slot_label(slot_name)}。"
