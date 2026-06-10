# Synapse 重构待办任务

本文档记录当前距离调通完整 `/turns` 服务还缺少的任务，并按建议优先级排序。

## P0：打通最小闭环

### 1. Runtime Wiring

目标：让 `src/synapse/api/main.py` 能装配真实的 `SynapseConductor`，不再只有空 handler。

交付物：

- 新增 Synapse runtime factory。
- 装配 Pre-Recognition、Recognition、Slot、Planning 的最小 pipeline。
- 保持 `/turns` request/response 兼容。

验收要点：

- `/turns` 能通过可注入 handler 返回 plan。
- 默认 runtime 可用 fake dependency 做端到端测试。
- 不修改 `src/copilot_kernel`。

### 2. Themis Recognition Step

目标：把 Themis 意图识别结果接入 Synapse pipeline。

交付物：

- 初始化 `BusinessIntentRecognizer`。
- 调用 Themis public API 获取 decision。
- 将 decision 写入 `intent_decision` artifact。
- 处理 low confidence、ambiguous intent 等提前回复场景。

验收要点：

- 不直接导入 `intent_fusion`。
- 识别结果只通过 `decision.verdict`、`decision.slot_operations`、`decision.action_intents` 路由。
- 覆盖 fake recognizer 单元测试。

### 3. Slot Operation Adapter

目标：把 Themis 的 slot operations 转换为 Synapse 内部 `SlotOperation`。

交付物：

- 建立 `entity_type` 到 `SlotRef` 的映射规则。
- 支持 replace/add/remove/clear。
- 识别 invalid slot target，并保留 clarify 所需上下文。

验收要点：

- adapter 保持薄层转换，不承载 domain 候选加载逻辑。
- 无效 target 可生成后续 clarify 输入。
- 覆盖 replace、multi slot、invalid target 测试。

## P1：Slot 状态与澄清能力

### 4. Slot Validation And Commit

目标：完成 slot 校验和会话状态提交。

交付物：

- 校验 required slots、dependencies、multi 约束。
- 支持候选有效性校验。
- 生成 `SlotStateDiff`，并投影为 plan 中的 `slot_state_diff`。
- 新增 `SlotCommitter`。

验收要点：

- slot state 只由 committer 修改。
- validate 失败时不提交部分状态。
- rollback/diff 行为有测试覆盖。

### 5. Session State Store

目标：跨 turn 保存 committed slots、pending clarify 和 confirmation 状态。

交付物：

- 以 `session_id` 为 key 的 session store protocol。
- 提供内存实现用于本地和测试。
- 支持 pending slot bundle 与 pending task。

验收要点：

- 同一 session 可延续 slot state。
- clarify 后的用户补充能合并到原任务。
- 不把 domain 规则写进通用 session 层。

### 6. Clarify Plan Builder

目标：基于缺失或非法 slot 构建统一的 `clarify` plan。

交付物：

- 为 missing_slots 和 invalid_slots 生成对应 prompts。
- prompts 必须包含 candidates。
- 支持 low confidence、ambiguous slot、missing slot、invalid slot reason。

验收要点：

- 每个 missing/invalid slot 都有 prompt。
- prompt 结构保持统一，无 `view` 字段。
- 无候选时显式失败或进入可解释 blocked 状态，不静默 fallback。

## P2：业务域与外部系统隔离

### 7. SigmaGateway

目标：抽象 Synapse 与 SigMA 业务系统交互，降低业务耦合。

交付物：

- 新增 `src/synapse/integrations/sigma` protocol。
- 定义 sensors、test_segments、indicator_names 查询接口。
- 提供 fake implementation 测试。

验收要点：

- domain 层只依赖 gateway protocol。
- 外部错误保留上下文，不宽泛吞掉。
- 不在通用 pipeline 中出现 SigMA 业务细节。

### 8. Observation Slot Domain

目标：为数据观察域补齐 slot schema、候选加载和 domain policy。

交付物：

- 定义 observation slot schema/catalog。
- 通过 SigmaGateway 加载候选。
- 实现 `sensors`、`test_segments`、`indicator_names` 的候选和校验规则。

验收要点：

- domain 常量留在 observation domain 模块。
- candidate resolver 不构建 plan。
- 覆盖空候选、多候选、非法候选测试。

### 9. Task Catalog Loader

目标：正式加载 `configs/tasks/*.yaml`，替代旧 actions 配置路径。

交付物：

- 新增 tasks config loader。
- 聚合多个 task definition YAML。
- 接入 runtime factory。

验收要点：

- 不复用旧 `configs/copilot.yaml` 的 `includes.actions` 语义。
- task YAML 使用当前 `TaskDefinition` 结构。
- YAML 错误能给出明确配置路径和 task name。

## P3：计划构建完善与端到端稳定

### 10. Composite Plan Builder

目标：统一调度 reply、clarify、confirm、task 等 plan builder。

交付物：

- 根据 recognition、slot validation、confirmation、task readiness 选择 plan kind。
- `TaskPlanBuilder` 只负责 ready task plan。
- reply/clarify/confirm 拆到独立 builder 或 policy。

验收要点：

- builder 不承载 resolver 和 session 写入职责。
- plan 均包含常驻 `slot_state_diff`。
- no-match、blocked、needs_confirmation 行为可测试。

### 11. End-To-End `/turns` Tests

目标：用 fake Themis 和 fake SigmaGateway 验证完整流转。

交付物：

- 覆盖 request -> conductor -> recognition -> slots -> planning -> response。
- 覆盖缺 slot 澄清、补充 slot 后生成 task、无匹配回复。
- 覆盖 slot_state_diff 输出。

验收要点：

- 测试不依赖真实外部服务。
- 保持 `/turns` 顶层响应 `{ "plan": ... }`。
- legacy `copilot_kernel` 行为不受影响。

### 12. Production Switch Plan

目标：准备 Phase 9 的生产切换方案，确保可回滚。

交付物：

- 明确旧 runtime 与 Synapse runtime 的切换开关。
- 记录回滚路径。
- 补充 smoke test。

验收要点：

- 生产切换不需要回滚已稳定 framework 代码。
- 切回旧 handler 的路径清晰。
- 切换前后 `/turns` 兼容面保持一致。
