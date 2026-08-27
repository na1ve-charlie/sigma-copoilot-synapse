# SigMA 接口对接说明

> 面向其他系统开发者与 Codex。本文描述的是当前 Maia 应用**实际调用**的 SigMA 后端 HTTP 接口；不包含 Maia 对外的 `/turns` 接口和 LLM 接口。代码实现是最终事实来源。

## 1. 全局约定

| 项目 | 当前约定 |
|---|---|
| Base URL | `SIGMA_BASE_URL`，默认 `http://192.168.0.65:8081`；拼接前去掉末尾 `/` |
| 鉴权 | 请求头 `Token: <SIGMA_TOKEN>`；不是 `Authorization` / `Bearer` |
| JSON 请求 | POST 接口使用 UTF-8 JSON，并发送 `Content-Type: application/json` |
| 语言 | 大部分接口使用 `lang`；来自 `workspace_context.lang`，缺省为 `zh` |
| 成功条件 | HTTP `< 400`，且响应 `code` 为 `0`、`200`、`"0"` 或 `"200"` |
| 通用响应 | 推荐 `{ "code": 200, "msg": "ok", "data": ... }` |
| 默认超时 | 除 Excel 导出为 3600 秒外，其余客户端为 5 秒 |
| 重试 | 仅“试验记录查询”识别 `code=1001`：用 `data` 中的新 token 更新鉴权并重试一次 |

除特别说明外，返回非 JSON、HTTP 错误或非成功业务码都会使当前任务失败。多数响应只要求 `data` 形状满足本说明；未声明的字段会被忽略或原样透传。

## 2. 接口总览

| # | 方法 | Path | 用途 |
|---:|---|---|---|
| 1 | GET | `/api/storage/singleStationReport/listReportByMulti` | 查询试验记录 |
| 2 | GET | `/api/storage/type` | 查询产品配置 |
| 3 | GET | `/api/storage/type/listVersions` | 查询产品版本 |
| 4 | GET | `/api/storage/type/listSystemNos` | 查询检测系统 |
| 5 | POST | `/api/storage/dataGroup/saveDataGroup` | 创建数据集 |
| 6 | POST | `/api/storage/dataGroup/saveSelectedResult` | 覆盖数据集中的记录 |
| 7 | GET | `/api/storage/resultData/getResultExistMap` | 查询数据集可观察数据 |
| 8 | POST | `/api/storage/config/listOneIndicatorsByResult` | 查询一维指标 |
| 9 | POST | `/api/storage/config/listLineIndicatorsByResult` | 查询二维线指标 |
| 10 | POST | `/api/storage/config/listMultiLineIndicatorsByResult` | 查询二维多线指标 |
| 11 | GET | `/api/storage/config/sensor-list` | 查询 Excel 可导出传感器 |
| 12 | POST | `/api/storage/singleStationReport/export` | 导出 Excel |
| 13 | POST | `/api/storage/dataGroup/listOriginDataInfoByResultIdList` | 结果 ID 转原始数据 ID |
| 14 | POST | `/api/storage/originData/OriginExport` | 导出原始数据 |
| 15 | POST | `/api/storage/originData/ngAudionCreated` | 生成 NG 音频 |
| 16 | POST | `/api/storage/dataFile/exportData` | 试验数据备份/删除 |

注意：`ngAudionCreated` 和 `OriginExport` 的大小写/拼写来自现有后端契约，不要自行修正。

## 3. 记录查询与产品目录

### 3.1 查询试验记录

`GET /api/storage/singleStationReport/listReportByMulti`

查询参数均放在 URL。数组序列化为逗号分隔字符串，布尔值序列化为小写 `true` / `false`。

| 参数 | 类型/默认值 | 含义 |
|---|---|---|
| `lang` | string / `zh` | 语言 |
| `page`, `rows` | int / `1`, `5000` | 页码、每页数量，必须大于 0 |
| `archive`, `keepLast`, `onlyRepeatSerial` | boolean / 均为 `false` | 归档、仅保留末次、仅重复序列号 |
| `type` | string | 产品类型 |
| `versionList`, `systemNoList` | CSV string | 配置版本、检测系统 |
| `serialNo`, `sumList` | string / CSV string | 序列号、汇总结果；应用会规范为：`不合格`、`合格`、`未设置界限值`、`异常`、`次异常`、`检测失败` |
| `manualTagging`, `status` | string | 当前允许：`合格`、`不合格`、`无效` |
| `remark`, `testSection` | string | 备注、试验段 |
| `sensorIdList`, `testNameList`, `indicatorList` | CSV string | 传感器、工况/试验名、指标 |
| `hasPdfReport`, `hasOriginData`, `hasResultData`, `hasColorMap` | boolean | 数据产物存在性过滤 |
| `startTime`, `endTime` | string | 时间范围；应用当前发送 ISO 日期/时间文本 |

当前映射器不会发送空数组或 `null`。模型中保留了 `dataGroupId`，但当前应用调用链固定不发送它。

最小成功响应：

```json
{"code":0,"msg":"ok","data":{"total":1,"list":[{"recordId":"46704"}]}}
```

`data.total` 必须是非负整数，`data.list`（兼容别名 `rows`）必须是数组，且每行必须有记录 ID。行字段兼容关系如下：

| 应用字段 | SigMA 可返回字段 |
|---|---|
| 记录 ID | `recordId` / `reportId` / `id`（必需） |
| 试验时间 | `testedAt` / `testTime` / `createdAt` |
| 产品/版本 | `productType`；或从 `type` 的最后一个 `_` 拆分；`configVersion` / `version` |
| 检测系统、序列号 | `systemNo` / `system`；`serialNumber` / `serialNo` |
| 汇总结果、手工标签 | `summaryResult` / `sum`；`manualTagList` / `manualTags` / `manualTagging` |
| 归档、重复序列号 | `archiveStatus`；`repeatSerial` |
| 可用产物 | `availableArtifacts` / `artifactKinds` / `dataKinds`，值支持 raw/result/report/audio/colormap；也兼容对应布尔标志 |

特殊鉴权响应：`{"code":1001,"data":"new-token"}` 会触发一次原请求重试；`code=1000` 等其他值按业务错误处理。

### 3.2 产品配置、版本和系统

| 接口 | Query | 要求的 `data` |
|---|---|---|
| `GET /api/storage/type` | `page=1&rows=99999&lang=zh` | `{ "rows": [...] }`；每行须有 `type`（兼容 `name`）、`version`（兼容 `versionName`）、`systemNo`；可选 `updateTime`，格式 `YYYY-MM-DD HH:MM:SS` |
| `GET /api/storage/type/listVersions` | `typeList=<产品类型>&lang=zh` | 数组；数字或字符串均会规范为字符串并去重 |
| `GET /api/storage/type/listSystemNos` | `typeList=<产品类型>&lang=zh` | 字符串数组，按返回顺序去重 |

## 4. 数据集物化

选择结果保存到 SigMA 时按顺序调用：创建数据集（已有 `dataset_id` 时跳过）→ 覆盖数据集记录。

### 4.1 创建数据集

`POST /api/storage/dataGroup/saveDataGroup`

```json
{"lang":"zh","name":"maia-<selection_hash前12位>"}
```

数据集 ID 可返回为 `data.dataGroupId`、`data.datasetId`、`data.id` 或直接放在 `data` 中；应用统一转为字符串。空选择且没有已有数据集 ID 时，两个接口都不会调用。

### 4.2 覆盖数据集记录

`POST /api/storage/dataGroup/saveSelectedResult?lang=zh`

```json
{"id":1172,"info":null,"name":"maia-session","resultList":[{"colorId":null,"resultId":46704,"serialNo":"SN-46704","testTime":"2026-06-11 19:38:34","version":"2","systemNo":"SYS-1","type":"dm0608"}],"copyStatus":false}
```

`id` / `resultId` 为纯数字文本时发送 JSON number，否则发送 string。`testTime` 固定为 `YYYY-MM-DD HH:MM:SS`。该接口是**全量覆盖**语义，已有数据集也可能收到空 `resultList`。

## 5. 数据观察目录

### 5.1 查询可用数据

`GET /api/storage/resultData/getResultExistMap?dataGroupId=<数据集ID>`

响应 `data` 是 `dataType → sensor → testName[]` 的三层映射：

```json
{"code":200,"data":{"ONE_D":{"Vib1":["1500rpm"]},"TWO_D_CEP":{"sensor02":["Spd-rCH"]}}}
```

当前支持的数据类型：`ONE_D`、`TWO_D_TD`、`TWO_D_FS`、`TWO_D_OS`、`TWO_D_OC`、`TWO_D_CEP`、`TWO_D_PS`。

### 5.2 查询指标

三个接口请求体相同；`ONE_D` 不发送 `dataType`，其他类型发送。应用会对上一步展开出的每个 `(dataType, sensor, testName)` 组合发起一次查询。

```json
{"sensorList":["Vib1"],"testNameList":["Spd-rDL"],"typeSystemVOList":[{"type":"dm0608_5","systemNo":"SYS-1"}],"dataType":"TWO_D_FS"}
```

| 选择条件 | 接口 |
|---|---|
| `dataType == ONE_D` | `POST /api/storage/config/listOneIndicatorsByResult?lang=zh` |
| `dataType == TWO_D_OC` | `POST /api/storage/config/listMultiLineIndicatorsByResult?lang=zh` |
| 其他二维类型 | `POST /api/storage/config/listLineIndicatorsByResult?lang=zh` |

`typeSystemVOList[].type` 是 `产品类型_配置版本`。响应推荐为 `{"code":200,"data":[{"name":"倒谱","index":"cep-index"}]}`；也兼容根数组。名称可用 `name` / `indicatorName` / `label`，索引可用 `index` / `indicatorIndex` / `value` / `id`，缺任一字段的条目会被忽略。

## 6. Excel 导出

调用顺序：查询传感器 → 用户选择 → 导出。一个导出请求只能包含同一产品、版本和检测系统下的记录。

### 6.1 查询传感器

`GET /api/storage/config/sensor-list?type=<产品类型_配置版本>&systemNo=<系统>&lang=zh`

成功响应的 `data` 为传感器 ID 数组；应用按顺序去重。

### 6.2 导出 Excel

`POST /api/storage/singleStationReport/export?lang=zh`

```json
{"type":"dm0608_5","systemNo":"SYS-1","idList":[46704,46703],"sensorIdList":["Torque","sensor01"],"oneData":1,"twoData":1,"resultData":1}
```

`type` 必须是 `产品类型_配置版本`；两个数组都不能为空，ID 必须为正整数。`oneData`、`twoData`、`resultData` 只允许 `0`/`1`，分别控制一维数据、二维数据、结果数据。当前已观察到 `data=true` 和 `data=["<xlsx-url>", ...]` 两种成功形态；应用不二次解释，原样放入任务结果。此接口客户端超时为 3600 秒。

## 7. 原始数据导出

这是强制两阶段调用，不能把试验结果 ID 直接传给最终导出接口。

1. `POST /api/storage/dataGroup/listOriginDataInfoByResultIdList?lang=zh`，请求体是结果 ID 数组，如 `[29181,29182]`。
2. 从返回行的正整数 `id` 取出原始数据 ID，再调用 `POST /api/storage/originData/OriginExport?lang=zh`。

查询响应推荐 `{"code":200,"data":[{"id":30191}]}`；当前也兼容根数组，以及嵌套在 `data` / `rows` / `list` / `content` 中的数组。没有 ID 或 ID 非正整数会终止导出。

最终请求：

```json
{"idList":[30191],"path":"D:\\exportOriginFile","dataExportType":1,"systemNo":"SYS-1"}
```

`dataExportType`：`0=H5`，`1=TDMS`；`path` 当前固定为 SigMA 服务端路径 `D:\exportOriginFile`。成功响应通常为 `{"code":200,"data":true}`。

## 8. NG 音频生成

`POST /api/storage/originData/ngAudionCreated?lang=zh`

请求体直接是正整数结果 ID 数组，例如 `[46467,46478]`，不是对象。成功响应样例：`{"code":200,"data":null}`。

## 9. 试验数据备份与删除

`POST /api/storage/dataFile/exportData?lang=zh`

```json
{"resultIdList":[46704,46703],"colorMap":true,"originData":true,"resultData":false,"dataExportType":2,"filePath":"D:/数据备份/","fileName":"backup-001"}
```

| 字段 | 规则 |
|---|---|
| `resultIdList` | 非空正整数结果 ID 数组 |
| `colorMap`, `originData`, `resultData` | 至少一个必须为 `true` |
| `dataExportType` | `1=删除`、`2=备份`、`3=备份后删除` |
| `filePath` | 非空服务端路径，当前默认 `D:/数据备份/` |
| `fileName` | 类型 2/3 必需，类型 1 禁止；不得含 Windows 非法字符/控制字符，不得以空格或点结尾，也不得使用保留设备名（如 `CON`、`COM1`） |

该操作在应用层始终需要用户确认；成功响应通常为 `{"code":200,"data":true}`。

## 10. 对接实现检查清单

1. 完整保留 Path 大小写和现有拼写，使用 `Token` 请求头。
2. GET 数组参数按 CSV 接收，POST 按 UTF-8 JSON 接收；业务成功同时返回 HTTP 2xx 和成功 `code`。
3. 记录查询保证 `data.total`、`data.list` 和每行记录 ID；产品配置保证三项必需字段。
4. 明确区分结果 ID、数据集 ID、原始数据 ID；实现原始数据导出的两阶段 ID 转换。
5. 把 `path` / `filePath` 视为 **SigMA 服务端文件系统路径**，不是 Maia 所在机器或浏览器路径。
6. Excel 导出可能长时间运行；代理、网关和调用方超时应不低于 3600 秒，或共同改为异步任务契约。
7. 新增/修改接口时同步更新适配器测试和本文；不要只修改离线样例。

## 11. 代码事实来源

- 端点与 HTTP 适配：`src/maia/integrations/sigma/*.py`
- 记录查询参数/响应兼容：`request_mapper.py`、`response_mapper.py`
- 字段业务语义：`src/maia/tasks/*_policy.py`、`test_record_management.py`
- 可执行请求样例：`turns_offline_data/sigma/backend_endpoint_samples.json`
- 契约测试：`tests/test_maia_sigma_*.py`

复核优先级：**适配器代码 > 测试 > 本文 > 离线样例**。例如离线样例中的 `rows=500` 不是当前默认值；当前代码默认值是 `5000`。
