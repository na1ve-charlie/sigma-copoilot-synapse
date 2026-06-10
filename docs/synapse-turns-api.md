# Synapse Turns API — 联调文档

> 版本: 0.1.0  
> 接口: `POST /turns`  
> 应用: Sigma Copilot — NVH 数据观测域

---

## 目录

1. [接口概述](#1-接口概述)
2. [请求格式 (TurnRequest)](#2-请求格式-turnrequest)
3. [响应格式 (TurnResponse)](#3-响应格式-turnresponse)
4. [处理流水线](#4-处理流水线)
5. [响应 Plan 种类详解](#5-响应-plan-种类详解)
6. [Session 状态管理](#6-session-状态管理)
7. [边缘情况与错误处理](#7-边缘情况与错误处理)
8. [配置清单](#8-配置清单)
9. [典型联调场景](#9-典型联调场景)
10. [常见问题 FAQ](#10-常见问题-faq)

---

## 1. 接口概述

| 项目 | 内容 |
|------|------|
| 方法 | `POST` |
| 路径 | `/turns` |
| Content-Type | `application/json` |
| 请求体 | `TurnRequest` (JSON) |
| 成功响应 | `HTTP 200` — `TurnResponse` |
| 校验失败 | `HTTP 422` — Pydantic 校验错误 |
| 服务未就绪 | `HTTP 503` — handler 未配置 |

**核心语义**：用户在一次对话"回合"(turn)中发送一条消息，服务端经过意图识别 → Slot 解析 → 执行计划构建 后，返回一个 `plan`。前端根据 `plan.kind` 做不同交互。

---

## 2. 请求格式 (TurnRequest)

### 2.1 完整 Payload 示例

```json
{
  "session_id": "sess-001",
  "message": "查看传感器 S1 的频谱",
  "workspace_context": {
    "workspace_session_id": "ws-abc",
    "data_load_mode": "dataset",
    "dataset_id": "1152",
    "dataset_name": "MAXV 1152",
    "dataset_origin": "selected_dataset",
    "dataset_version": 3,
    "filter_hash": "abc123",
    "products": [
      {
        "product_type": "P1",
        "product_version": "V1",
        "system_no": "SYS-01"
      }
    ],
    "test_time": {
      "start": "2026-05-01",
      "end": "2026-05-31"
    },
    "type_systems": [
      {
        "type": "P1",
        "system_no": "SYS-01"
      }
    ],
    "lang": "zh"
  }
}
```

### 2.2 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `session_id` | `string` | **是** | 会话标识，用于关联上下文状态 |
| `message` | `string` | **是** | 用户本次自然语言输入 |
| `workspace_context` | `object` | 否 | 工作空间上下文，传递数据集/过滤器等信息 |

### 2.3 WorkspaceContext 字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `workspace_session_id` | `string` | 否 | 工作空间会话 ID |
| `data_load_mode` | `"dataset" \| "filter"` | 否 | 数据加载模式 |
| `dataset_id` | `string` | 否 | 数据集 ID |
| `dataset_name` | `string` | 否 | 数据集名称 |
| `dataset_origin` | `"selected_dataset" \| "copilot_filter"` | 否 | 数据来源 |
| `dataset_version` | `integer` | 否 | 数据集版本 |
| `filter_hash` | `string` | 否 | 过滤器哈希 |
| `products` | `array[ProductContext]` | 否 | 产品列表，默认 `[]` |
| `test_time` | `TimeRange` | 否 | 测试时间范围 |
| `type_systems` | `array[TypeSystemContext]` | 否 | 类型-系统列表，默认 `[]` |
| `lang` | `string` | 否 | 语言，默认 `"zh"` |

### 2.4 约束

- **所有 Model 都设置 `extra="forbid"`** — 请求中出现未定义的字段会返回 `422`。
- **无 `user_id` 字段** — 旧版有 `user_id`；新版如果传入会返回 `422`。
- `session_id` 不可为空字符串。

---

## 3. 响应格式 (TurnResponse)

### 3.1 标准响应

```json
{
  "plan": {
    "kind": "reply",
    "message": "...",
    "data": {},
    "suggestions": [],
    "slot_state_diff": {
      "changes": []
    }
  }
}
```

### 3.2 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `plan` | `object` | 执行计划，前端根据 `plan.kind` 路由交互 |
| `plan.kind` | `string` | 计划类型，决定交互模式 |

> 内部字段（`status`, `message`, `diagnostics` 等）**不会暴露**在最终响应中。API 层会自动提取 `plan` 输出。

---

## 4. 处理流水线

`POST /turns` 进入后按以下步骤依次执行。每一步都可能提前终止流水线并返回 plan。

```
TurnRequest
  │
  ├─ [1] TaskContextLoaderStep        — 加载 session 的任务上下文
  ├─ [2] CandidateCatalogStep         — 从 Sigma 加载候选值目录
  ├─ [3] PreRecognitionStep           — 实体别名归一化，缩小候选范围
  ├─ [4] ThemisRecognitionStep        — 调用 Themis LLM 意图识别
  │   ├─ verdict=low       → 返回 ReplyPlan（低置信度）
  │   ├─ verdict=ambiguous → 返回 ClarifyPlan（模糊意图）
  │   └─ verdict=high      → 继续
  ├─ [5] ObservationIndicatorInferenceStep  — 推断 indicator 和数据域 (仅 Sigma 模式)
  │   └─ scope conflict     → 返回 ClarifyPlan（域冲突/无效）
  ├─ [6] SlotResolutionStep           — Themis slot_operations → 内部 SlotOperation
  ├─ [7] SlotValidationStep           — 校验候选值合法性
  │   └─ 校验不通过          → 返回 ClarifyPlan（invalid_slots）
  ├─ [8] SlotCommitterStep            — 原子提交 slot 变更到 session 状态
  ├─ [9] PlanningStep                 — 匹配任务定义，构建执行计划
  │   ├─ clear_context               → ContextClearPlan
  │   ├─ current_context             → ReplyPlan（当前上下文）
  │   ├─ resolver_query              → ReplyPlan（查询候选值列表）
  │   ├─ 未匹配任务 + slot 有变化    → ContextUpdatePlan
  │   ├─ 未匹配任务 + slot 无变化    → ReplyPlan("No matching task.")
  │   ├─ 匹配任务 + 缺少必要 slot    → ClarifyPlan(missing_slots)
  │   └─ 匹配任务 + slot 齐全        → TaskPlan
  └─ [10] TaskContextCommitterStep    — 持久化任务上下文（即使提前返回也执行）
```

**关键规则**：一旦某个 step 设置了 `context.plan`，后续步骤除标记了 `run_after_plan=True` 的之外都会被跳过。目前 `TaskContextCommitterStep` 是唯一标记 `run_after_plan` 的步骤。

---

## 5. 响应 Plan 种类详解

### 5.1 `kind: "reply"`

**语义**：纯文本回复，无待定交互。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `kind` | `"reply"` | 是 |  |
| `message` | `string` | 是 | 回复文本 |
| `data` | `object` | 否 | 附加数据，默认 `{}` |
| `suggestions` | `array[string]` | 否 | 建议的下一条输入，默认 `[]` |
| `slot_state_diff` | `object` | 否 | 本次变更的 slot diff |

**出现场景**：
- Themis 低置信度 → `"我还没有识别出明确的业务意图。"`
- 查询当前上下文 → `"Current context."` + `data.slots`
- resolver_query 查询 → `"Available {slot_name}."` + `data.candidates`
- 未匹配任务且 slot 未变化 → `"No matching task."`

**示例**：
```json
{
  "plan": {
    "kind": "reply",
    "message": "Available sensors.",
    "data": {
      "slot_name": "sensors",
      "candidates": ["S1", "S2", "S3"]
    },
    "suggestions": ["S1", "S2", "S3"],
    "slot_state_diff": { "changes": [] }
  }
}
```

---

### 5.2 `kind: "clarify"`

**语义**：需要用户进一步澄清（模糊意图、缺失参数、无效参数）。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `kind` | `"clarify"` | 是 |  |
| `reason` | `string` | 是 | 澄清原因：`low_confidence` / `ambiguous_intent` / `missing_slots` / `invalid_slots` / `ambiguous_slots` |
| `message` | `string` | 是 | 提示文本 |
| `pending_task` | `string` | 否 | 待定任务名称，用于追踪 |
| `missing_slots` | `array[string]` | 否 | 缺失的 slot 名列表 |
| `invalid_slots` | `array[string]` | 否 | 无效的 slot 名列表 |
| `prompts` | `array[Prompt]` | 否 | 需要前端展示的选项 |
| `suggestions` | `array[string]` | 否 | 建议输入 |
| `slot_state_diff` | `object` | 否 | 本次变更的 slot diff |

**Prompt 对象**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `string` | 对应 slot 名称 |
| `target` | `"slot" \| "intent" \| "text"` | 交互目标 |
| `label` | `string` | 显示标签 |
| `message` | `string` | 提示消息 |
| `required` | `boolean` | 是否必填 |
| `input_type` | `"single_select" \| "multi_select" \| "text"` | 输入类型 |
| `candidates` | `array[PromptCandidate]` | 候选值列表 |

**PromptCandidate 对象**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `value` | `any` | 值 |
| `label` | `string` | 显示标签 |
| `description` | `string` | 描述（可选） |
| `disabled` | `boolean` | 是否禁用，默认 `false` |

**出现场景**：
- `ambiguous_intent`: Themis 判定意图模糊，无法确定用户想做什么
- `missing_slots`: 匹配到任务但必要 slot 未填写，需要用户补充
- `invalid_slots`: slot 值不在候选列表中
- `ambiguous_slots`: 观察域中推断 indicator 时 data_type 有歧义

**示例 (missing_slots)**：
```json
{
  "plan": {
    "kind": "clarify",
    "reason": "missing_slots",
    "message": "Required task parameters are missing.",
    "pending_task": "query_frequency_spectrum",
    "missing_slots": ["sensors", "test_segments"],
    "prompts": [
      {
        "id": "sensors",
        "target": "slot",
        "label": "sensors",
        "message": "Select sensors.",
        "required": true,
        "input_type": "multi_select",
        "candidates": [
          { "value": "S1", "label": "S1" },
          { "value": "S2", "label": "S2" }
        ]
      },
      {
        "id": "test_segments",
        "target": "slot",
        "label": "test_segments",
        "message": "Select test_segments.",
        "required": true,
        "input_type": "multi_select",
        "candidates": [
          { "value": "TS-01", "label": "TS-01" },
          { "value": "TS-02", "label": "TS-02" }
        ]
      }
    ],
    "slot_state_diff": { "changes": [] }
  }
}
```

---

### 5.3 `kind: "task"`

**语义**：任务已就绪，可执行。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `kind` | `"task"` | 是 |  |
| `status` | `"ready" \| "needs_confirmation"` | 是 | 任务状态 |
| `name` | `string` | 是 | 任务标识（编程用） |
| `title` | `string` | 是 | 任务标题（展示用） |
| `risk_level` | `"low" \| "medium" \| "high"` | 是 | 风险等级 |
| `requires_confirmation` | `boolean` | 是 | 是否需要用户确认 |
| `params` | `object` | 是 | 执行参数（由 slot 值和 domain provider 填充） |
| `message` | `string` | 是 | 文本说明 |
| `reason` | `string` | 否 | 原因说明 |
| `slot_state_diff` | `object` | 否 | slot 变更记录 |

**出现场景**：
- 匹配到 `data_observation` 下的某个任务（如 `query_frequency_spectrum`），且必需的 slot（`sensors`, `test_segments`, `indicator_names`）都已填写

**示例**：
```json
{
  "plan": {
    "kind": "task",
    "status": "ready",
    "name": "query_frequency_spectrum",
    "title": "Query frequency spectrum",
    "risk_level": "low",
    "requires_confirmation": false,
    "params": {
      "sensors": ["S1"],
      "test_segments": ["TS-01"],
      "indicator_names": [
        {"name": "RMS", "index": "RMS-xxx"}
      ],
      "data_types": ["TWO_D_FS"]
    },
    "message": "Task is ready: Query frequency spectrum",
    "slot_state_diff": {
      "changes": [
        {
          "slot": "sensors",
          "before": null,
          "after": ["S1"]
        }
      ]
    }
  }
}
```

---

### 5.4 `kind: "confirm"`

**语义**：需要用户确认某个操作。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `kind` | `"confirm"` | 是 |  |
| `reason` | `string` | 是 | 确认原因 |
| `message` | `string` | 是 | 提示文本 |
| `payload` | `object` | 否 | 待确认的数据 |
| `slot_state_diff` | `object` | 否 | slot 变更记录 |

**出现场景**：任务定义中 `requires_confirmation: true` 且 status=needs_confirmation 时，前端应展示确认界面。

---

### 5.5 `kind: "context_update"`

**语义**：上下文已更新，非任务性回复。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `kind` | `"context_update"` | 是 |  |
| `message` | `string` | 是 | 提示文本 |
| `projected_slots` | `object` | 否 | 当前所有 slot 值快照 |
| `slot_state_diff` | `object` | 否 | 本次 slot 变更 |

**出现场景**：用户在会话中切换了 sensor / test_segment / indicator，但未触发任何任务匹配。例如："切换到传感器 S2" → Themis 识别为 switch_sensor → slot 更新 → 无匹配任务 → ContextUpdatePlan。

---

### 5.6 `kind: "context_clear"`

**语义**：清除当前会话上下文。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `kind` | `"context_clear"` | 是 |  |
| `message` | `string` | 是 | 默认 `"Context cleared."` |
| `preserved` | `array[string]` | 否 | 保留的上下文项 |
| `cleared` | `array[string]` | 否 | 已清除的上下文项 |
| `slot_state_diff` | `object` | 否 | slot 变更记录 |

**出现场景**：用户说"清除上下文"或"重置" → Themis 识别为 `task.nvh.context_management.clear_context`。

---

### 5.7 slot_state_diff 结构

所有 plan 都包含 `slot_state_diff`，记录本次 turn 对 slot 的变更。

```json
{
  "changes": [
    {
      "slot": "sensors",
      "before": null,
      "after": ["S1"]
    },
    {
      "slot": "indicator_names",
      "before": ["RMS"],
      "after": ["Peak"]
    }
  ]
}
```

---

## 6. Session 状态管理

### 6.1 状态类型

| 状态 | 作用域 | 生命周期 | 存储位置 |
|------|--------|----------|----------|
| SlotState | 每个 session_id | 内存，随应用重启清空 | `SlotCommitterStep._states` |
| TaskContextState | 每个 session_id | 内存 | `InMemoryTaskContextStore` |
| CandidateCatalog | 每次请求 | 请求级 | TurnContext artifacts |

### 6.2 Slot 状态变更规则

- `replace` — 替换值
- `add` — 追加到列表
- `remove` — 从列表删除
- `clear` — 清空单个 slot
- 特殊 intent `clear_context` — 清空所有 slot

### 6.3 任务上下文状态

| 字段 | 说明 |
|------|------|
| `pending_task_name` | clarify 流程中待定的任务名，从 `ClarifyPlan.pending_task` 获得 |
| `active_task_name` | 已就绪的任务名，从 `TaskPlan.name` 获得 |

**状态转换**：
- `clarify` plan 带 `pending_task` → 设置 `pending_task_name`
- `task` plan 带 `status=ready/needs_confirmation` → 设置 `active_task_name`，清除 `pending_task_name`
- `context_clear` plan → 清除所有状态

---

## 7. 边缘情况与错误处理

### 7.1 HTTP 层面

| 状态码 | 触发条件 | 说明 |
|--------|----------|------|
| `200` | 正常处理完成 | 始终返回 `{"plan": {...}}` |
| `422` | 请求体包含未定义字段 / 字段类型错误 | Pydantic 校验错误 |
| `503` | Turn handler 未配置 | `"Turn handler is not configured"` |

### 7.2 业务层提前结束

| 阶段 | 条件 | Plan 结果 |
|------|------|-----------|
| Themis 识别 | `verdict=low` | `ReplyPlan("我还没有识别出明确的业务意图。")` |
| Themis 识别 | `verdict=ambiguous` | `ClarifyPlan(reason="ambiguous_intent")` |
| Slot 校验 | 值不在候选列表中 | `ClarifyPlan(reason="invalid_slots")` + 候选 prompt |
| Indicator 推断 | data_type 冲突/无效 | `ClarifyPlan(reason="ambiguous_slots"/"invalid_slots")` |
| 计划构建 | 无匹配任务 + slot 无变化 | `ReplyPlan("No matching task.")` |
| 计划构建 | 缺少必要 slot | `ClarifyPlan(reason="missing_slots")` + 候选 prompt |

### 7.3 内部异常（应避免在生产中发生）

| 异常 | 触发条件 | 说明 |
|------|----------|------|
| `KeyError` | PlanningStep 缺少 `intent_decision` 或 `slot_state` artifact | 上游步骤配置错误 |
| `TypeError` | artifact 类型不符合预期 | 上下游类型不匹配 |
| `ValueError` | slot validation 未通过就提交了 commit | 步骤顺序错误 |
| `ValueError` | ClarifyPlan 的 missing_slots / invalid_slots 缺少对应的 prompt | 构建计划时的代码错误 |

### 7.4 Sigma 依赖不可用

如果 Sigma HTTP 网关超时或不可用：

- CandidateCatalog 将为空（`CandidateCatalog()`），由 `CandidateCatalogStep` 兜底
- 无候选值 → `GenericSlotValidator` 跳过校验 → slot 值直接通过
- 无候选值 → `TaskPlanBuilder._slot_prompt()` 在缺少 candidates 时抛出 `KeyError`
- IndicatorInferenceStep 和 ResolverQueryHandler 在非 Sigma 模式下不会被注入

### 7.5 空消息

`message` 为空字符串时：
- Themis 将收到空消息，大概率返回 `verdict=low` → `ReplyPlan`

---

## 8. 配置清单

### 8.1 主配置 `configs/copilot.yaml`

```yaml
recognition:
  llm:
    model: "qwen/qwen3-4b-2507"         # LLM 模型
    base_url: "http://localhost:1234/v1" # LLM 服务地址
    temperature: 0.1
    max_tokens: 800
    retries: 2
  themis:
    alpha: 0.10                          # 意图识别置信度阈值
    delta: 0.10                          # 歧义阈值
    min_intent_score: 0.50
    build_index_on_init: true

sigma:
  base_url: "http://192.168.0.65:8081"   # Sigma 后端地址
  default_lang: "zh"
  timeout: 5.0
```

### 8.2 意图定义 `configs/themis/intents/*.yaml`

每个 intent 包含：`name`, `domain`, `embed_text`, `tree_text`。

示例文件：
- `nvh_data_observation.yaml` — 数据观察任务（频谱、时域、阶次谱等）
- `nvh_context_management.yaml` — 上下文管理（清除/查看上下文）
- `nvh_resolver_query.yaml` — 候选值查询（可用 sensor / segment / indicator）
- `nvh_chat.yaml` — 聊天/咨询意图
- `nvh_data_management.yaml` — 数据管理

### 8.3 任务定义 `configs/tasks/data_observation.yaml`

```yaml
query_frequency_spectrum:
  intent_names:
    - "task.nvh.data_observation.batch.frequency_spectrum"
  title: "Query frequency spectrum"
  risk_level: "low"
  requires_confirmation: false
  required_slots:
    - "sensors"
    - "test_segments"
    - "indicator_names"
  optional_slots: []
```

所有 NVH 观察任务（7 个）共享相同的 `required_slots`。

### 8.4 Slot 定义 `configs/slots/nvh.yaml`

| Slot | Entity Type | Required | Multi | Resolver |
|------|-------------|----------|-------|----------|
| `data_types` | `data_type` | 否 | 否 | `observation_availability` |
| `sensors` | `sensor` | 是 | 是 | `observation_availability` |
| `test_segments` | `test_segment` | 是 | 是 | `observation_availability` |
| `indicator_names` | `indicator` | 否 | 否 | `observation_indicators` |

### 8.5 Resolver 配置 `configs/resolvers/nvh.yaml`

| Resolver | 后端接口 | 用途 |
|----------|----------|------|
| `observation_availability` | `/api/storage/resultData/getResultExistMap` | 获取可用的 sensor / test_segment / data_type |
| `observation_indicators` | `/api/storage/config/listLineIndicatorsByResult` (默认) | 获取 indicator 候选值 |

---

## 9. 典型联调场景

### 场景 1: 用户直接提出完整任务

> 用户： "查看传感器 S1 在 TS-01 的 RMS 频谱"
>
> 请求：
> ```json
> { "session_id": "s1", "message": "查看传感器 S1 在 TS-01 的 RMS 频谱" }
> ```

**预期执行路径**：
1. Themis 识别 → `verdict=high`, intent=`task.nvh.data_observation.batch.frequency_spectrum`
2. Slot 解析 → sensors=[S1], test_segments=[TS-01], indicator_names=[RMS]
3. Slot 校验 → 通过
4. Slot 提交 → 更新 session slot state
5. 计划构建 → task `query_frequency_spectrum`，所有 required slots 齐全

**预期响应**：
```json
{
  "plan": {
    "kind": "task",
    "status": "ready",
    "name": "query_frequency_spectrum",
    "title": "Query frequency spectrum",
    "params": {
      "sensors": ["S1"],
      "test_segments": ["TS-01"],
      "indicator_names": [
        {"name": "RMS", "index": "RMS-xxx"}
      ],
      "data_types": ["TWO_D_FS"]
    },
    "message": "Task is ready: Query frequency spectrum"
  }
}
```

---

### 场景 2: 用户逐步补充参数

> 第一轮： "切换到传感器 S2"
> 第二轮： "看频谱"

**第一轮预期**：
- Themis 识别 intent switch_sensor + 查找频谱（未明确）或 context_update
- Slot: sensors=[S2]
- Plan: `context_update`

```json
{ "plan": { "kind": "context_update", "message": "Context updated.", "projected_slots": { "sensors": ["S2"] } } }
```

**第二轮预期**（session state 已有 sensors=[S2]）：
- Themis 识别 `frequency_spectrum`
- Slot: sensors 已存在，从 session state 继承
- Plan: 检查 required slots → 缺少 test_segments, indicator_names → `clarify(missing_slots)`

```json
{
  "plan": {
    "kind": "clarify",
    "reason": "missing_slots",
    "missing_slots": ["test_segments", "indicator_names"],
    "pending_task": "query_frequency_spectrum",
    "prompts": [ /* test_segments 和 indicator_names 的候选 */ ]
  }
}
```

---

### 场景 3: 查询可用候选值

> 用户： "有哪些传感器可用"

**预期**：
- Themis 识别 `inquiry.nvh.resolver_query.sensors`
- 不走任务匹配，直接返回候选列表

```json
{
  "plan": {
    "kind": "reply",
    "message": "Available sensors.",
    "data": {
      "slot_name": "sensors",
      "candidates": ["S1", "S2", "S3", "S4"]
    },
    "suggestions": ["S1", "S2", "S3", "S4"]
  }
}
```

---

### 场景 4: 用户输入模糊意图

> 用户： "你好"

**预期**：
- Themis 低置信度 → `ReplyPlan`

```json
{ "plan": { "kind": "reply", "message": "我还没有识别出明确的业务意图。" } }
```

---

### 场景 5: 用户清除上下文

> 用户： "清除上下文"

**预期**：
- Themis 识别 `task.nvh.context_management.clear_context`
- Slot 全清除
- Task 上下文清除

```json
{ "plan": { "kind": "context_clear", "message": "Context cleared." } }
```

---

### 场景 6: 用户输入无效 slot 值

> 用户： "查看传感器 NONEXIST 的频谱"

**预期**：
- Themis 识别 intent + sensor 值
- Slot 校验发现 `NONEXIST` 不在候选列表中 → `clarify(invalid_slots)`

```json
{
  "plan": {
    "kind": "clarify",
    "reason": "invalid_slots",
    "invalid_slots": ["sensors"],
    "prompts": [
      {
        "id": "sensors",
        "target": "slot",
        "label": "sensors",
        "message": "Select sensors.",
        "required": true,
        "input_type": "multi_select",
        "candidates": [ /* 合法候选值列表 */ ]
      }
    ]
  }
}
```

---

### 场景 7: 第二轮补齐参数

续场景 2 第二轮后，用户在第三轮给出缺失参数。

> 第三轮： "TS-02 和 TS-03，指标 RMS 和 Peak"

**预期**：
- Themis 识别为 slot 填充意图（replace test_segments + indicator_names）
- Slot 解析 → test_segments=[TS-02, TS-03], indicator_names=[RMS, Peak]
- 加上 session 中的 sensors=[S2]
- 全部 required slots 齐全 → `TaskPlan`

---

### 场景 8: 带完整 workspace_context 的工作流

前端在打开某个数据集时传入完整的 workspace_context，请求中的 dataset_id 将作为 Sigma resolver 查询的参数。

- `observation_availability` resolver 会从 workspace_context 的 `dataset_id` 读取 `dataGroupId`
- 返回对应数据集的可用 sensor/test_segment/data_type 列表
- 如果 workspace_context 缺失 `dataset_id`，Sigma resolver 可能返回空结果或报错

---

### 场景 9: 用户切换数据域但未明确任务

> 用户： "切换到时域数据"

**预期**：
- Themis 识别 `time_domain` 意图
- Slot 更新 data_type
- 现有 session 中 sensors / test_segments / indicator_names 保持不变
- 如果缺少其他 slot → `clarify(missing_slots)`
- 如果全部齐全 → `TaskPlan`

---

### 场景 10: Sigma 后端不可用

当 Sigma HTTP 接口超时或返回错误码时：

- `SigmaCandidateCatalogLoader` 会抛出异常 → 取决于调用处是否捕获
- 可观察行为：候选值为空 → 校验跳过 → 任务计划中 slot prompt 可能因缺少 candidates 而失败
- **联调时务必确保 Sigma 服务可达**

---

## 10. 常见问题 FAQ

**Q: 为什么请求中不能带 `user_id`？**  
A: 新版 API 去掉了 `user_id` 字段，且 model 配置了 `extra="forbid"`。传入 `user_id` 会返回 `422`。

**Q: 返回的 plan 字段有哪些可能的值？**  
A: `kind` 目前有 6 种：`reply`, `clarify`, `confirm`, `task`, `context_update`, `context_clear`。

**Q: Session 状态是持久化的吗？**  
A: 当前是**纯内存**存储。应用重启后所有 session 状态会丢失。这需要在后续版本中对接持久化存储。

**Q: 如何处理多轮对话时的参数继承？**  
A: 每一轮处理时，`SlotCommitterStep` 会从 `_states` 中读取该 session 的 slot state。新值被追加或替换，旧值被继承。无需前端额外传入已填参数。

**Q: 前端如何区分展示类型的 Plan？**  
A: 根据 `plan.kind`：
- `reply` → 直接展示 `message`
- `clarify` → 展示 `message` + 渲染 `prompts`（单选/多选/文本输入）
- `task` → 展示 `message` + 准备执行任务（`status=ready` 直接执行；`needs_confirmation` 先展示确认界面）
- `confirm` → 展示确认对话框
- `context_update` / `context_clear` → 展示 `message` + 刷新上下文状态

**Q: 从前端角度看，`slot_state_diff` 应该怎么用？**  
A: 主要用于前端展示 slot 的变更记录（哪些值被改了、从什么改成什么）。前端可以用它做状态同步的校验，但不是必须依赖的字段。

**Q: 联调时如何快速判断问题在哪一步？**  
A: 可以根据 plan 的 kind + reason 推断：
- `reply` + `"我还没有识别出明确的业务意图"` → Themis 置信度低 → 检查 LLM / intent 配置
- `clarify` + `ambiguous_intent` → 多个 intent 分数接近 → 检查 intent YAML 的 `tree_text`
- `clarify` + `missing_slots` → 匹配到任务但缺参数 → 正常的自然流程
- `clarify` + `invalid_slots` → 值不在候选列表中 → 检查 Sigma 候选数据或用户输入
- `task` 没出来 → 检查 task 配置中的 `intent_names` 是否包含 Themis 输出的 intent name
