# Themis

Themis 是面向业务 Copilot 的意图识别与路由决策模块。它封装底层
`intent-fusion` 的 embedding recall 与 LLM tree reasoning，只向业务调用方暴露稳定的结果模型：
识别结论、原子意图序列、slot 操作、终止动作意图和 diagnostics。

使用者通常只需要接入三个东西：

1. 意图定义文件：声明业务 handler 能处理哪些意图。
2. LLM 适配器：提供一个 OpenAI-compatible 服务，或实现最小 `chat` 接口。
3. Resolver：把当前工作区可用实体注入模型，并校验 slot 是否有效。

## 目录结构

| 路径 | 说明 |
| --- | --- |
| `themis/` | 业务方应依赖的公开 API |
| `cli.py` | 本地调试命令行入口，安装后也可用 `themis` 命令 |
| `configs/maia/runtime/intents/*.yaml` | Maia runtime intent definitions. |
| `tests/config/cases.yaml` | 示例标注用例，用于人工回归或扩展测试集 |
| `vendor/intent-fusion/` | 底层融合库，业务代码不建议直接依赖 |

## 快速开始

同步依赖：

```powershell
uv sync
```

查看 CLI 参数：

```powershell
uv run themis --help
```

使用默认示例意图文件进入交互式调试：

```powershell
uv run themis --base-url http://localhost:1234/v1 --model qwen/qwen3-4b-2507
```

识别单条消息并输出 JSON：

```powershell
uv run themis `
  --intents .\tests\config\intents.yaml `
  --base-url http://localhost:1234/v1 `
  --model qwen/qwen3-4b-2507 `
  --message "去掉 sensor_1 加上 sensor_2" `
  --json
```

查看 CLI 内置的本地 mock resolver 值：

```powershell
uv run themis --show-mock
```

## 接入流程

1. 复制 `configs/maia/runtime/intents/*.yaml` 到业务仓库中的配置目录。
2. 为每个业务 handler 增加稳定唯一的 intent name。
3. 写清 `embed_text` 和 `tree_text`，尤其是相近意图之间的边界。
4. 接入 LLM。生产环境推荐使用稳定的 OpenAI-compatible endpoint。
5. 接入 Resolver。至少为可枚举实体提供当前可用值，例如 `sensor`、`indicator`、`test_segment`。
6. 在业务入口初始化 `BusinessIntentRecognizer`，服务启动时可选择预热 embedding 索引。
7. 根据 `decision.verdict`、`decision.slot_operations` 和 `decision.action_intents` 构建应用层计划。
8. 为高频说法、边界说法和负例补充回归测试。

## 最小业务代码

推荐业务方只依赖 `themis` 包，不直接 import `intent_fusion` 类型。

```python
import asyncio

from themis import (
    BusinessIntentRecognizer,
    OpenAICompatibleLLM,
    RecognitionConfig,
    ResolverMocker,
)


async def main() -> None:
    llm = OpenAICompatibleLLM(
        base_url="http://localhost:1234/v1",
        model="qwen/qwen3-4b-2507",
        api_key="not-needed",
        temperature=0.1,
        max_tokens=800,
    )
    resolver = ResolverMocker({
        "sensor": ["sensor_1", "sensor_2", "sensor_3"],
        "test_segment": ["idle", "acceleration", "cruise"],
        "indicator": ["RMS", "2Ord", "peak"],
    })
    recognizer = BusinessIntentRecognizer.from_yaml(
        "configs/maia/runtime/intents/nvh_terminal_actions.yaml",
        llm,
        resolver=resolver,
        config=RecognitionConfig(
            alpha=0.10,
            delta=0.10,
            min_intent_score=0.50,
            build_index_on_init=True,
        ),
    )

    decision = await recognizer.recognize("去掉 sensor_1 加上 sensor_2")
    if decision.verdict.value == "low":
        return await fallback_to_chat(decision)
    if decision.requires_confirmation:
        return await ask_user_to_confirm(decision)

    plan = await build_application_plan(decision)
    return await execute_application_plan(plan)


asyncio.run(main())
```

## 路由处理

`verdict` 是第一层路由策略：

| verdict | 含义 | 建议处理 |
| --- | --- | --- |
| `clear` | 单意图或复合意图足够明确 | 交给应用层 PlanBuilder 构建执行计划 |
| `ambiguous` | 存在真实竞争候选 | 展示确认或澄清 |
| `low` | 没有可信业务意图 | 不进入业务 handler，转闲聊、搜索或澄清 |

Themis 不生成应用级 `execution_plan`。应用层应基于识别结果构建自己的
`ApplicationExecutionPlan`：

```python
async def build_application_plan(decision):
    context_updates = build_context_updates(decision.slot_operations)
    projected_context = preview_context_updates(context_updates)
    actions = build_actions(decision.action_intents)
    slot_requests = await resolve_missing_slots(actions, projected_context)

    if slot_requests.missing_required:
        return ClarifyPlan(slot_requests=slot_requests)

    return ExecutePlan(
        context_updates=context_updates,
        actions=actions,
        resolved_slots=slot_requests.values,
    )
```

推荐执行顺序固定为：preview 上下文修改、补全缺失 slots、校验、提交上下文修改、执行最终 action。

## 返回结果模型

常用属性：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `decision.verdict` | `RecognitionVerdict` | `clear`、`ambiguous`、`low` |
| `decision.intents` | `tuple[IntentMatch, ...]` | 模型输出的意图列表，保留原始顺序 |
| `decision.slot_operations` | `tuple[SlotOperation, ...]` | 面向执行的 slot 操作列表，按 `entity_type` 合并 |
| `decision.action_intents` | `tuple[IntentMatch, ...]` | 没有显式 slot 操作的终止动作意图 |
| `decision.diagnostics` | `RecognitionDiagnostics` | 融合层调试信息，不作为执行依据 |
| `decision.requires_confirmation` | `bool` | `ambiguous` 时为 `True` |
| `decision.degraded` | `bool` | 底层识别通道降级时为 `True` |
| `decision.to_dict()` | `dict` | JSON 可序列化结果，包含业务语义结果和 diagnostics |

`IntentMatch`：

| 字段 | 含义 |
| --- | --- |
| `name` | 业务 intent name |
| `score` | 融合后的识别分数，已四舍五入到 4 位小数 |
| `slots.action` | 操作类型，例如 `replace`、`add`、`remove` |
| `slots.entity_type` | 实体类型，例如 `sensor`、`indicator`、`test_segment` |
| `slots.target` | 实体值，例如 `sensor_1` |
| `slots.slot_valid` | Resolver 校验结果 |

`diagnostics`：

| 字段 | 含义 |
| --- | --- |
| `top_candidate` | 底层融合分最高的候选，结构为 `{name, score}` |
| `runner_up` | 底层融合次高候选，结构为 `{name, score}` |
| `degraded` | 是否发生底层通道降级 |

## Slot Operations 协议

`decision.intents` 是模型识别出的原子意图，可能包含多条相同 intent。
`decision.slot_operations` 是给业务执行用的视图：

- 没有 slot 的意图不会出现在 `slot_operations` 中。
- 相同 `entity_type` 的操作会合并为 1 个 `SlotOperation`。
- 合并后 `action`、`target`、`slot_valid` 使用数组，顺序与模型输出一致。
- `intent` 相同则保持标量；如果同一实体类型来自不同 intent，则为数组。
- `score` 相同则保持标量；不同则为数组。
- 不同 `entity_type` 不合并，保持首次出现顺序。

例如用户输入：

```text
去掉 sensor_1 加上 sensor_2
```

`slot_operations` 会是：

```json
[
  {
    "intent": "task.nvh.context_management.switch_sensor",
    "score": 1.0,
    "action": ["remove", "add"],
    "entity_type": "sensor",
    "target": ["sensor_1", "sensor_2"],
    "slot_valid": [true, true]
  }
]
```

跨实体类型的输入不会合并到同一条：

```text
去掉 idle 加上 sensor_1
```

```json
[
  {
    "intent": "task.nvh.context_management.switch_test_segment",
    "score": 1.0,
    "action": "remove",
    "entity_type": "test_segment",
    "target": "idle",
    "slot_valid": true
  },
  {
    "intent": "task.nvh.context_management.switch_sensor",
    "score": 1.0,
    "action": "add",
    "entity_type": "sensor",
    "target": "sensor_1",
    "slot_valid": true
  }
]
```

业务执行时可以统一把字段转成数组处理：

```python
def as_list(value):
    return list(value) if isinstance(value, (list, tuple)) else [value]


async def switch_sensor(decision):
    for operation in decision.slot_operations:
        if operation.entity_type != "sensor":
            continue
        actions = as_list(operation.action)
        targets = as_list(operation.target)
        valids = as_list(operation.slot_valid)

        for action, target, valid in zip(actions, targets, valids):
            if not valid:
                return await ask_user_to_select_valid_sensor(target)
            await apply_sensor_operation(action, target)
```

## 意图文件配置

意图文件使用 YAML。开发期可以直接参考 `configs/maia/runtime/intents/*.yaml`，生产环境建议放在业务自己的
`configs/` 目录中。

顶层可以是 `intents` 列表：

```yaml
intents:
  - name: "task.nvh.context_management.switch_sensor"
    domain: "nvh.context_management"
    embed_text: "切换传感器, 传感器换成, 加上传感器, 不看传感器, sensor_1, S1, S2"
    tree_text: "用户想修改当前观察上下文中的传感器筛选条件。添加、移除、替换传感器都应匹配该意图。"

  - name: "task.nvh.data_observation.batch.frequency_spectrum"
    domain: "nvh.data_observation"
    embed_text: "频谱, spectrum, 频谱数据, 频谱图, 传感器频谱"
    tree_text: "用户想查看普通频谱数据。不要把阶次谱、阶次切片或业务概念咨询误分到该意图。"

  - name: "chat.nvh.capabilities"
    domain: "nvh.chat"
    embed_text: "你好, hello, 能力说明, 你能做什么, 帮助"
    tree_text: "用户询问助手能力或闲聊，不应启动业务 Flow。"
```

字段说明：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `name` | 是 | 稳定、唯一、可路由的业务意图 ID |
| `domain` | 否 | 业务域，用于构造 LLM tree 分组 |
| `embed_text` | 否 | embedding 通道召回文本；未配置时使用 `name` |
| `tree_text` | 否 | LLM tree 裁决文本；未配置时回退到 `embed_text` |

编写建议：

1. `name` 是 handler 契约，确定后尽量不要改名。
2. `embed_text` 多写用户真实说法、缩写、英文别名和关键词。
3. `tree_text` 写清语义边界、反例和执行条件。
4. 相近意图要成对写边界，例如“什么是阶次谱”和“查看阶次谱”。
5. 暂不支持的领域建议显式建 `*.unsupported`，防止误路由。
6. 复合意图中的每个原子动作都应有可路由 intent。

## Resolver 接入

Resolver 用于两件事：

1. 把当前工作区可用实体注入 LLM prompt，帮助模型选择正确 slot。
2. 对模型抽取出的 `entity_type + target` 做校验，产出 `slot_valid`。

自定义 Resolver 只需要实现异步 `resolve`：

```python
class WorkspaceResolver:
    async def resolve(
        self,
        entity_type: str,
        context: dict | None = None,
    ) -> list[str]:
        workspace_id = (context or {}).get("workspace_id")
        if entity_type == "sensor":
            return await load_workspace_sensors(workspace_id)
        if entity_type == "test_segment":
            return ["idle", "acceleration", "cruise"]
        if entity_type == "indicator":
            return ["RMS", "2Ord", "peak"]
        return []
```

传入 recognizer：

```python
recognizer = BusinessIntentRecognizer.from_yaml(
    "configs/maia/runtime/intents/nvh_terminal_actions.yaml",
    llm,
    resolver=WorkspaceResolver(),
)
```

内置 Resolver：

| 类 | 使用场景 |
| --- | --- |
| `ResolverMocker` | 本地开发和测试 |
| `EnumResolver` | 枚举值来自配置 |
| `HttpResolver` | 枚举值来自业务 HTTP API |
| `CompositeResolver` | 组合多个 resolver |

配置式构建：

```python
from themis import build_resolver


resolver = build_resolver({
    "enum": {
        "case_sensitive": False,
        "values": {
            "sensor": ["sensor_1", "sensor_2", "sensor_3"],
            "indicator": ["RMS", "2Ord", "peak"],
        },
    },
    "http": {
        "base_url": "http://business-service",
        "endpoints": {
            "test_segment": {
                "path": "/api/test-segments",
                "params": {"workspace": "{workspace_id}"},
                "items_path": "data.items",
                "value_key": "id",
            }
        },
    },
    "merge": True,
})
```

HTTP Resolver 返回值支持：

- JSON 数组：`["sensor_1", "sensor_2"]`
- 对象数组：`[{"id": "sensor_1"}, {"id": "sensor_2"}]`
- 嵌套对象：通过 `items_path` 指到数组位置，通过 `value_key` 指到字段名

`context` 会包含当前消息、intent、融合后的 score 和 action 等信息；业务 Resolver 可以利用这些字段做权限、
工作区或页面上下文过滤。

## LLM 接入

使用 OpenAI-compatible endpoint：

```python
from themis import OpenAICompatibleLLM


llm = OpenAICompatibleLLM(
    base_url="http://localhost:1234/v1",
    model="qwen/qwen3-4b-2507",
    api_key="not-needed",
)
```

使用 OpenAI 官方服务时可以不传 `base_url`，客户端会读取 `OPENAI_API_KEY`：

```python
llm = OpenAICompatibleLLM(model="your-openai-chat-model")
```

自定义 LLM 只需实现：

```python
from typing import Any


class MyLLM:
    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        return await call_your_model(messages, **kwargs)
```

LLM 返回内容由底层 `intent-fusion` prompt 约束为 JSON。业务方通常不需要自己拼 prompt。

## 参数调优

Themis 的运行参数在 `RecognitionConfig` 中配置，也可以通过 CLI 参数传入。

| 参数 | 默认值 | 作用 | 调整建议 |
| --- | --- | --- | --- |
| `alpha` | `0.10` | embedding 通道权重，LLM tree 权重为 `1 - alpha` | LLM 稳定时保持低值；短词或关键词匹配更重要时调高 |
| `delta` | `0.10` | 第一名与第二名分差达到该值才判为 `clear` | 歧义过多时调低；误执行过多时调高 |
| `min_intent_score` | `0.50` | 低于该融合分的分类意图不暴露给业务方 | 复合意图漏出时调低；噪声子意图过多时调高 |
| `embedding_model` | `Qwen/Qwen3-Embedding-0.6B` | sentence-transformers embedding 模型 | 生产环境建议固定并提前缓存 |
| `build_index_on_init` | `False` | 初始化 recognizer 时预构建 embedding 索引 | 服务启动阶段可接受预热时设为 `True` |

CLI 调参示例：

```powershell
uv run themis `
  --alpha 0.15 `
  --delta 0.12 `
  --min-intent-score 0.60 `
  --warm-up
```

调参建议：

1. 先固定模型、意图文件、resolver 值和测试集，再改参数。
2. 优先用 `tree_text` 修正边界问题，用 `delta` 调整保守程度。
3. 不要只看意图准确率，也要检查 `ambiguous` 和 `low` 的交互成本。
4. 改意图文件后跑回归用例，确认高频说法、边界例和负例都稳定。

## 本地调试技巧

打印可用 mock 实体：

```powershell
uv run themis --show-mock
```

使用自定义 mock resolver 文件：

```yaml
values:
  sensor: ["sensor_1", "sensor_2"]
  test_segment: ["idle", "cruise"]
  indicator: ["RMS", "peak"]
```

```powershell
uv run themis --mock-values .\mock-values.yaml --message "只看 sensor_2"
```

交互式调试时重点看：

- `verdict` 是否符合产品策略。
- `slot operations` 是否按实体类型合并。
- `action intents` 是否包含最终动作。
- `diagnostics` 是否解释了底层 top 和 runner-up；runner-up 只是调试候选，不能作为执行依据。
- `slot_valid=false` 是否来自 resolver 值缺失或模型抽取错误。

## 回归用例

`tests/config/cases.yaml` 是标注样例模板。单意图示例：

```yaml
cases:
  - message: "查看 RMS 指标值"
    expected: "task.nvh.data_observation.indicator_query.value"
    note: "查询指标值"
```

复合意图示例：

```yaml
cases:
  - message: "去掉 sensor_2 加上 sensor_1"
    expected:
      - "task.nvh.context_management.switch_sensor"
      - "task.nvh.context_management.switch_sensor"
    slots:
      - {action: "remove", entity_type: "sensor", target: "sensor_2"}
      - {action: "add", entity_type: "sensor", target: "sensor_1"}
    note: "同域批量：移除并添加传感器"
```

建议至少覆盖：

1. 高频自然语言说法。
2. 中英文混输、缩写、实体别名。
3. 相近意图边界。
4. 未支持功能的负例。
5. 多 slot、同实体类型批量操作、跨实体类型组合操作。

## 开发验证

运行 public API 单元测试：

```powershell
uv run python -m unittest tests.test_public_api
```

编译检查：

```powershell
uv run python -m py_compile `
  cli.py `
  themis\__init__.py themis\config.py themis\errors.py `
  themis\models.py themis\loaders.py themis\llm.py `
  themis\recognizer.py themis\resolver.py `
  tests\test_public_api.py
```

CLI smoke test：

```powershell
uv run themis --help
```

## 常见问题

### 为什么 `decision.intents` 里有多条相同 intent？

复合指令会被拆成多个原子动作，例如“去掉 sensor_1 加上 sensor_2”。这些原子动作在
`decision.intents` 中保留原始顺序，便于调试和追踪。
业务执行建议由应用层 PlanBuilder 消费 `decision.slot_operations` 和
`decision.action_intents`。同一 `entity_type` 会被合并成一条数组化操作。
如果观察动作只出现在 `diagnostics.runner_up`，说明它还不是可执行语义结果；需要通过意图文案或
tree prompt 让它进入 `intents/action_intents`。

### `slot_valid=false` 怎么处理？

说明模型抽取到了实体，但 Resolver 没有返回该值。常见处理方式是向用户展示可选值并要求确认，
或者刷新 Resolver 数据后重试。不要静默执行无效 slot。

### 首次请求为什么慢？

embedding 通道第一次使用时需要加载 sentence-transformers 模型并构建索引。生产环境可开启
`build_index_on_init=True`，并在部署镜像或机器上提前缓存 `embedding_model`。

### 什么时候需要更新 `tree_text`？

当错误集中在相近意图边界时，优先更新 `tree_text`，写清“应该进”和“不应该进”的条件。
当召回不到某类表达时，再补 `embed_text`。
