# Sigma Maia Windows 生产部署

## 发布包说明

发布物位于 `dist\windows\SigmaMaia`。请复制整个目录，不能只复制
`SigmaMaia.exe`。目标机需要 64 位 Windows 10/11，不需要安装 Python；建议至少
8 GB 内存，并确保能访问 SigMA 后端和配置的 OpenAI-compatible LLM 服务。

发布包包含 Python 运行时、应用依赖和 Qwen embedding 模型，但不包含对话 LLM
服务。默认 LLM 地址仍是 `http://localhost:1234/v1`，目标机没有该服务时，必须把
`configs\maia\runtime\recognition.yaml` 中的 `base_url` 改为可访问的地址。

## 构建

只能在 64 位 Windows 上构建 Windows 包。在仓库根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-windows.ps1
```

脚本会先运行测试，再用固定版本的 PyInstaller 构建，复制外置配置和本机已缓存的
`Qwen/Qwen3-Embedding-0.6B` 模型，最后执行配置和离线模型自检。构建机需要 Python 3.12、
`uv`、网络依赖缓存以及该 embedding 模型缓存。

临时调试可使用 `-SkipTests` 或 `-SkipModel`，正式发布不应使用这两个参数。

## 首次部署

1. 将完整的 `SigmaMaia` 目录复制到固定位置，例如 `C:\SigmaMaia`。
2. 修改 `config\app.env` 和 `configs\maia\runtime\recognition.yaml`。
3. 双击 `check-config.cmd`，看到 `configuration OK` 后再继续。
4. 前台试运行可双击 `start.cmd`，访问 `http://127.0.0.1:8000/docs`。
5. 生产常驻运行：用“管理员身份”打开 PowerShell，在程序目录执行
   `.\install-service.ps1`。程序会随 Windows 启动，日志写入 `logs\maia.log`。

### 临时售前 Demo（目标机无需源码）

发布包已经包含 Demo 页面。将完整的 `SigmaMaia` 目录复制到测试机后，双击
`start-demo.cmd`，售前通过 `http://测试机IP:8000/` 访问。该脚本临时启用页面并监听
局域网地址；关闭窗口即停止，不会修改 `app.env`。当前 API 不带登录鉴权，只能在隔离
测试网络、VPN 或限制来源 IP 的防火墙规则后使用，不要直接暴露到公网。

API 冒烟检查：

```powershell
$body = @{session_id="smoke"; message="你好"} | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/turns `
  -Method Post -ContentType "application/json" -Body $body
```

## 修改机器配置

编辑 `config\app.env`，一行一个 `名称=值`，修改后重启程序：

| 配置 | 说明 | 默认值 |
| --- | --- | --- |
| `MAIA_HOST` | 监听地址；仅本机使用保持 `127.0.0.1` | `127.0.0.1` |
| `MAIA_PORT` | HTTP 端口 | `8000` |
| `MAIA_LOG_LEVEL` | `critical/error/warning/info/debug/trace` | `info` |
| `MAIA_ENABLE_DEMO` | `1` 启用内置 Demo 首页；生产默认关闭 | `0` |
| `SIGMA_ENABLE_MAIA` | `1` 启用；`0` 禁用处理器 | `1` |
| `SIGMA_BASE_URL` | SigMA 后端根地址 | `http://192.168.0.65:8081` |
| `SIGMA_TOKEN` | SigMA 访问令牌，可留空 | 空 |

操作系统中已经存在的同名环境变量优先于 `app.env`。不要提交真实 Token；限制
`app.env` 和 `recognition.yaml` 的文件读取权限。

## 修改识别配置

编辑 `configs\maia\runtime\recognition.yaml`：

- `llm.model`：对话模型名，必须与 LLM 服务一致。
- `llm.base_url`：OpenAI-compatible API 根地址，通常以 `/v1` 结尾。
- `llm.api_key`：LLM 服务密钥；不需要密钥时填服务接受的占位值。
- `temperature/max_tokens/retries`：推理温度、最大输出和重试次数。
- `themis.alpha/delta/min_intent_score`：识别融合阈值。
- `themis.build_index_on_init`：设为 `true` 会增加启动时间，但避免首个请求才加载模型。

意图定义在 `configs\maia\runtime\intents\*.yaml`，树提示词在
`configs\maia\runtime\tree_prompt.yaml`。这些文件可直接修改而无需重新打包，但必须
重启程序。修改意图、Prompt 或识别阈值前应备份原文件，并跑项目回归测试，避免改变
现有路由语义。

## 重启、停止和卸载

通过计划任务常驻运行时：

```powershell
Restart-ScheduledTask -TaskName SigmaMaia
Stop-ScheduledTask -TaskName SigmaMaia
```

永久取消开机启动，在管理员 PowerShell 中运行 `.\uninstall-service.ps1`。该脚本只
删除计划任务，程序目录、配置和日志会保留。

升级时先停止任务，备份 `config`、`configs` 和 `logs`，替换整个发布目录，再恢复经
确认仍兼容的配置并启动。回滚时停止任务并换回上一版完整目录；不要只替换 EXE。

若把 `MAIA_HOST` 改为 `0.0.0.0` 供局域网访问，还需要只对可信网段开放 Windows
防火墙端口，并在前置代理处理 TLS、鉴权和访问控制；当前 API 自身不提供这些边界保护。
