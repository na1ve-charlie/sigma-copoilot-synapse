# AGENTS.md

## 项目上下文

本仓库是一个 Python 应用，依赖本地可编辑安装的 `themis` 库。

- `themis` 依赖路径在 `pyproject.toml` 中声明为 `../themis`。
- 修改意图识别、路由、Resolver、LLM 接入、应用层执行计划构建前，必须先阅读 `docs/themis.md`。
- 业务代码只能依赖 `themis` 包公开 API。
- 禁止直接导入 `intent_fusion`。
- 核心接入流程是：
  1. 定义意图 YAML。
  2. 配置 LLM 适配器。
  3. 配置 Resolver。
  4. 初始化 `BusinessIntentRecognizer`。
  5. 根据以下字段做应用层路由：
     - `decision.verdict`
     - `decision.slot_operations`
     - `decision.action_intents`
- Themis 不生成应用级执行计划。
- 本应用需要基于识别结果自行构建执行计划。

## 当前维护目标

本仓库包含大量 AI 生成代码。

当前优先级不是继续扩展功能，而是：

1. 保持现有行为稳定。
2. 提高可维护性。
3. 补充 characterization tests。
4. 通过小步、可审查的变更降低风险。
5. 避免推测式重写。

## 通用规则

- 每次输出回答，请在开始时叫我的名字 - “Charlie-4-7”。
- 不要做大规模修改。
- 单次 diff （不包括测试代码）原则上不超过 260 行有效变更，允许有5%左右浮动，除非用户明确批准。
- 优先拆成多个小 PR，不要一次性提交大 PR。
- 除非用户明确要求，不要改变业务行为。
- 未经批准，不要修改公开 API、Pydantic Schema、YAML 格式、配置格式或外部响应格式。
- 不要格式化无关文件。
- 不要重命名文件、移动模块或调整包结构，除非用户明确要求。
- 不要新增依赖，除非用户明确批准。
- 不要做大范围 cleanup、大范围抽象或架构重写。
- 不要把重构和新功能混在一次改动里。
- 重构前优先补 characterization tests。
- 每次变更后必须说明：
  - 改动摘要
  - 修改文件
  - 行为影响
  - 风险点
  - 已运行测试
  - 回滚方式

## 禁止行为

除非用户明确要求，不要执行以下操作：

- 重写整个模块。
- 用新架构替换当前可工作的逻辑。
- 修改看起来像生成代码或兼容性敏感的代码，除非说明原因。
- 修改 Pydantic 模型字段名、默认值、校验器或序列化行为。
- 修改意图 YAML 的语义。
- 修改 Resolver 行为而不补测试。
- 修改 LLM Prompt 行为而不记录修改前后的预期差异。
- 添加会静默吞掉错误的 fallback 逻辑。
- 捕获宽泛异常但不记录日志或不保留上下文。
- 直接从 `intent_fusion` 导入任何对象。

## 重构规则

执行重构任务时：

1. 必须保持行为不变。
2. 复杂逻辑重构前，先补或更新测试。
3. 一次只重构一个模块或一条调用链。
4. 不要修改公开接口。
5. 不要修改数据 Schema。
6. 不要修改路由语义。
7. 保持 diff 小且可 Review。
8. 说明为什么该重构保持行为等价。
9. 说明如何回滚。

允许的小重构：

- 抽取小 helper 函数。
- 拆分长函数，但不改变行为。
- 补充类型标注。
- 优化局部变量命名。
- 为复杂函数补 docstring。
- 在已有测试覆盖下消除重复代码。

高风险重构，必须先获得用户明确批准：

- 移动文件。
- 重命名公开函数或类。
- 修改 Pydantic 模型。
- 修改 Resolver 契约。
- 修改路由逻辑。
- 修改意图 YAML 结构。
- 修改 LLM Prompt 模板。
- 修改应用层执行计划构建逻辑。

## God Code 防护规则

本项目禁止继续把多类职责堆进单个文件、单个类或单个方法。新增代码前必须先判断职责归属，优先保持现有行为稳定，而不是继续向已有大函数追加分支。

### 职责边界

- Orchestrator 只负责流程编排，不承载业务域规则、数据转换细节、候选视图生成或校验细节。
- Builder/Planner 只负责把输入模型转换为计划模型，不直接实现具体业务域策略。
- Resolver 只负责候选值解析和校验所需数据，不构建应用执行计划或对外响应。
- Renderer/View helper 只负责输出展示结构，不参与路由、slot 投影、session 状态变更或 action 决策。
- Session/Kernel 层负责会话生命周期和 active plan 状态，不把业务域细节下沉到通用编排逻辑。
- Domain policy/service 负责具体业务域规则。业务域专用常量、别名、候选视图和特殊补全逻辑应放在对应 domain 模块中。

### 复杂度阈值

以下情况必须先停下来拆分，或在回复中明确说明风险并请求用户批准：

- 单个方法超过 80 行有效逻辑。
- 单个类同时处理 3 类以上职责。
- 单个文件超过 500 行后还继续新增核心逻辑。
- 一个方法同时包含路由判断、状态变更、resolver 调用、schema 构造和业务域特判中的 3 类以上。
- 通用模块中出现具体业务域常量或业务域名称，例如指标域、传感器、频谱、工况等专用概念。
- 新增分支需要理解多个远端模块的隐式执行顺序才能判断是否安全。

超过阈值时，优先抽取小的私有 helper、policy class、factory 或 projector。不要为了赶进度继续扩大原方法。

### 新增逻辑前检查

新增分支或修改复杂函数前，必须回答：

1. 这个逻辑是通用规则，还是某个业务域规则？
2. 它是否会修改 session、active plan、slot_values 或其他上下文状态？
3. 它是否依赖 resolver 候选值或外部数据？
4. 它是否构造对外响应字段、Pydantic schema 或 candidate view？
5. 它是否改变路由、执行计划、fallback 或错误处理语义？

如果答案覆盖 2 类以上职责，必须拆到独立 helper/class，并补 characterization tests。不要把新职责直接塞进既有 orchestrator、builder、kernel 或 resolver。

### 模块增长规则

- 给已有大模块加代码时，优先新增旁路 helper 文件或局部 helper class，保持调用点小而清晰。
- 抽取 helper 时保持私有边界优先，不新增公开 API，除非用户明确批准。
- 不要在一次改动里同时做抽象、搬迁、行为修复和新功能。
- 不要通过宽泛 fallback、吞异常或隐式默认值掩盖职责不清的问题。
- 如果发现重复的业务域知识出现在 2 个以上模块，应先记录风险并补测试，再小步收敛到单一 owner。

### 测试要求

涉及复杂模块拆分或职责迁移时，必须优先补 characterization tests，至少覆盖：

- 输入对象和初始状态。
- 关键依赖的返回值，例如 resolver 候选。
- 输出 plan/status。
- 状态投影结果，例如 slot_values 或 active plan。
- 对外可见字段，例如 params、missing_slots、candidate_views、message。

禁止为了方便重构修改 Pydantic Schema、YAML 语义、配置格式或外部响应格式。

## 测试规则

修改复杂逻辑前，优先补 characterization tests，用来记录当前行为。

优先覆盖：

- 意图识别路由
- slot operations
- Resolver 行为
- 应用层执行计划构建
- 空候选
- 多候选
- 缺失 slot
- 非法输入
- 模糊意图
- LLM fallback 行为
- 错误处理

测试目标：

- Characterization tests 用于记录当前行为。
- Regression tests 用于防止已知问题复发。
- Unit tests 覆盖纯函数和小服务。
- Integration tests 覆盖主要请求到 decision 的流程。

不要为了方便写测试而修改业务代码，除非用户明确批准。

## 常用命令

优先使用项目已有命令。

常见命令：

```bash
python --version
pip install -e ../themis
pip install -e .
pytest
ruff check .
mypy .
