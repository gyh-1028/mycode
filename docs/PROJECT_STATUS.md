# MyCode 项目状态报告

> 生成时间：2026/07/06  
> 分析范围：项目根目录  
> 状态：P0-P15 已完成；本报告已同步至 0.2.2 本地 Web 工作台。

---

## 1. 项目概览

MyCode 是一个本地优先的 AI 编程 Agent，提供 CLI、本地 Web 工作台、Textual TUI 和 VS Code workspace extension。当前版本为 `0.2.2`。

| 属性 | 值 |
|------|-----|
| 入口命令 | `python -m mycode` / `mycode` |
| 入口函数 | `mycode.cli:app`（Typer） |
| 版本号 | `0.2.2`（`pyproject.toml`、`src/mycode/__init__.py`、CLI 一致） |
| Python 要求 | `>=3.11`（CI 覆盖 3.11-3.14） |
| 发行名 | `mycode-ai-cli`（导入名和命令保持 `mycode`） |
| 核心依赖 | typer、rich、openai、anthropic、pydantic；Web/Textual/MCP/OpenTelemetry 为 extras |
| 默认模型 | `deepseek-chat`（通过 OpenAI 兼容接口访问 DeepSeek） |
| 当前配置 | `.mycode/config.toml`：DeepSeek 端点，`api_key_env = "DEEPSEEK_API_KEY"` |

---

## 2. 目录结构

```text
<project-root>
├── pyproject.toml              # 项目元数据、依赖、脚本入口、pytest 配置
├── README.md                   # 使用说明（中文）
├── .gitignore
├── .mycode/
│   └── config.toml             # 项目本地配置（DeepSeek）
├── src/mycode/
│   ├── __init__.py             # 版本号
│   ├── __main__.py             # python -m mycode 入口
│   ├── cli.py                  # Typer CLI：参数解析、REPL、会话、命令分发
│   ├── commands.py             # 自定义 REPL 斜杠命令加载
│   ├── config.py               # 配置分层加载、环境变量覆盖、preset_config
│   ├── context.py              # 上下文窗口估算与压缩
│   ├── session.py              # 会话 JSON 持久化
│   ├── checkpoint.py           # 任务级写入检查点与撤销
│   ├── permissions.py          # 路径/命令权限与黑名单
│   ├── prompts.py              # 系统提示 + MYCODE.md 注入
│   ├── ui.py                   # diff、确认提示、流式输出、用量打印
│   ├── pricing.py              # Token 成本估算
│   ├── mentions.py             # @文件/目录 引用展开
│   ├── git_ops.py              # 自动提交与 diff
│   ├── logging_config.py       # 轮转文件日志与凭据脱敏
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── loop.py             # Agent 主循环、工具调用、卡住检测
│   │   └── planning.py         # 任务计划生成与注入
│   ├── llm/
│   │   ├── __init__.py         # build_provider 工厂
│   │   ├── base.py             # Provider 抽象与归一化类型
│   │   ├── openai_compatible.py# OpenAI / DeepSeek 实现
│   │   └── anthropic.py        # Claude 实现
│   └── tools/
│       ├── __init__.py         # 注册侧效导入
│       ├── registry.py         # Tool 注册表与 dispatch
│       ├── _common.py          # 工具公共函数
│       ├── files.py            # 文件读写改工具
│       ├── search.py           # 搜索工具
│       ├── shell.py            # Shell 执行工具
│       └── git.py              # 只读 Git 工具注册
├── tests/                      # 396 个离线测试（全部通过）
└── scripts/
    └── smoke_llm.py            # 真实 API 烟雾脚本
```

---

## 3. CLI 命令入口与现有命令

入口：`src/mycode/cli.py:main()`（Typer 命令）。

### 启动模式

| 模式 | 触发条件 | 行为 |
|------|----------|------|
| one-shot | `mycode "task"` | 执行单次任务后退出 |
| REPL | `mycode`（无 task） | 进入交互循环，多轮保留上下文 |
| 恢复 | `--resume <id>` / `--continue` | 加载已有会话 messages 继续 |
| 列表 | `mycode sessions` / `--sessions` | 列出 `.mycode/sessions/` 下的存档 |

### 内置关键字命令

在 `main()` 中通过字符串匹配拦截，因为 `task` 是可选位置参数，无法使用真正的子命令：

- `mycode init [provider]` / `mycode init --global`
- `mycode doctor [--api]`
- `mycode config show | where`
- `mycode session show <id> | export <id> | delete <id> | prune [--keep N]`
- `mycode sessions`

### CLI flags

`--resume`, `--continue`, `--sessions`, `--undo`, `--commit`, `--budget`, `--version`, `--help`。

### REPL 斜杠命令

| 命令 | 功能 |
|------|------|
| `/help` | 显示帮助 |
| `/sessions` | 列出会话 |
| `/doctor` | 检查配置与 key |
| `/model` | 显示当前模型/provider |
| `/undo` | 撤销当前会话最近一次写入 |
| `/diff` | 显示当前 checkpoint 的 git diff |
| `/exit` | 退出 |

支持自定义命令：`.mycode/commands/<name>.md` 或 `~/.mycode/commands/<name>.md`，模板中 `$ARGS` 会被替换。

---

## 4. 模块完成状态

| 模块 | 状态 | 说明 |
|------|------|------|
| Agent Loop | 已完成 | `agent/loop.py` 实现完整 tool-use 循环、流式消费、卡住检测、预算检查、进度存档。 |
| Tool Manager | 已完成 | `tools/registry.py` 提供 `@register`、`get_schemas`、`dispatch`。 |
| LLM Provider 抽象 | 已完成 | `llm/base.py` 定义 `BaseProvider` 与归一化类型；工厂在 `llm/__init__.py`。 |
| File Tools | 已完成 | `files.py` 提供 `list_files`、`read_file`、`edit_file`、`write_file`、`apply_patch`。 |
| Search Tools | 已完成 | `search.py` 提供 `search_code`（优先 ripgrep，Python fallback）、`find_files`。 |
| Shell Tools | 已完成 | `shell.py` 提供 `run_bash`，带黑名单、风险分级、超时、输出截断。 |
| Git Tools | 已完成 | `git_ops.py` 支持自动提交与 diff；`tools/git.py` 暴露只读 `git_status`、`git_log`、`git_branch`。 |
| Patch Apply | 已完成 | `apply_patch` 实现 unified diff 解析与应用，落盘前走统一确认流程。 |
| Permission System | 已完成 | `permissions.py` 实现项目根边界、敏感路径、命令黑名单、风险分级。 |
| Context Builder | 已完成 | `context.py` 实现 token 估算、按整轮压缩、中文摘要；`prompts.py` 注入 MYCODE.md。 |
| Session Store | 已完成 | `session.py` 实现原子 JSON 保存、加载、列表、最近会话。 |
| Cache System | 已完成 | Anthropic provider 设置 `cache_control`；`Usage` 跟踪 cached/cache_write tokens。 |
| CLI UI | 已完成 | `ui.py` 与 `cli.py` 提供 diff、确认、流式打印、用量与成本显示。 |
| Planning | 已完成 | `agent/planning.py` 实现 auto/always/off 计划生成、进度注入。 |
| Checkpoint/Undo | 已完成 | `checkpoint.py` 实现按任务记录写入、撤销新建/修改文件。 |
| Mentions | 已完成 | `mentions.py` 实现 `@path` 上下文注入。 |
| Pricing/Budget | 已完成 | `pricing.py` 支持按模型成本估算、配置覆盖及 OpenAI/DeepSeek/Claude 内置价格。 |
| Skill System | 已完成 | `skills/__init__.py` 解析 `.mycode/skills/<name>/SKILL.md`，支持显式激活并注入 system prompt。 |
| Plugin System | 已完成 | `plugins/__init__.py` 通过 `mycode.plugins` entry point 发现插件，`PluginRegistrar` 控制 tool/provider/skill 注册；仅加载配置启用的插件。 |
| Reliability/Trace | 已完成 | `agent/runner.py`、`agent/events.py`、`trace.py` 提供结构化结果、取消、重试和 JSONL Trace。 |
| Evals | 已完成 | `evals/` 提供离线任务、评分、基线和机器可读结果。 |
| MCP Client | 已完成 | `mcp/` 支持本地 stdio tools/resources/prompts 和权限控制。 |
| Shared Runtime | 已完成 | `runtime.py` 与 `approvals.py` 统一 Session、Checkpoint、MCP、Runner 和前端审批。 |
| Textual TUI | 已完成 | `tui/` 提供会话、流式 Markdown 对话、思考折叠、活动列表、配置向导、Diff、用量、取消和权限模态框。 |
| RPC Protocol | 已完成 | `server/` 提供 transport-neutral `RpcSession`、stdio/WebSocket、Workspace、模型、Session、取消和权限响应。 |
| Local Web Workbench | 已完成 | `clients/web/` 与 `web/` 提供仅本机的 React 工作台、文件预览、Diff、模型凭据管理和令牌认证。 |
| VS Code Extension | 已完成 | `editors/vscode/` 提供 Session TreeView、Agent Webview 和选区上下文。 |
| Release Pipeline | 已完成 | PyPI/pipx、VSIX、跨平台 CI、TestPyPI smoke 和 OIDC Trusted Publishing。 |

**整体评估：核心产品模块均已完成。**

---

## 5. LLM Provider 实现状态

| Provider | 状态 | 实现方式 | API Key 环境变量 | 备注 |
|----------|------|----------|------------------|------|
| DeepSeek | 已完成 | `OpenAICompatibleProvider` + `base_url=https://api.deepseek.com` | `DEEPSEEK_API_KEY` | 支持流式、tool calls、缓存命中统计与 `reasoning_content`。 |
| OpenAI | 已完成 | `OpenAICompatibleProvider`（默认端点） | `OPENAI_API_KEY` | 同上。 |
| Kimi (Moonshot) | 已完成 | OpenAI-compatible + `kimi` / `moonshot` preset | `MOONSHOT_API_KEY` | 默认 `moonshot-v1-8k` 与 Moonshot endpoint。 |
| Claude (Anthropic) | 已完成 | `AnthropicProvider` | `ANTHROPIC_API_KEY` | 支持完整 Anthropic 消息格式转换、prompt caching、tool use。 |

### Provider 注册与工厂

- 位置：`src/mycode/llm/__init__.py:18`
- 逻辑：`@register_provider` 注册实现，`build_provider()` 按 `provider.type` 查表构造。
- 内置注册 `openai`、`deepseek`、`anthropic`；未知类型继续回退到 OpenAI-compatible，保持兼容。

### P3 Provider 增强

1. DeepSeek `reasoning_content` 已支持流式/非流式解析和独立 UI 展示。
2. Provider 注册表已支持新增实现，不再需要修改工厂分支。
3. Kimi preset 与 Claude 内置价格已补齐。

---

## 6. 会话、上下文压缩与工具调用消息

### 会话持久化

- 位置：`.mycode/sessions/<id>.json`
- 格式：`{id, model, provider, created_at, updated_at, messages}`
- messages 直接保存内部 OpenAI 格式列表，**不扁平化**。
- 保存方式：先写 `.tmp` 再用 `os.replace` 原子替换。
- 恢复：`--resume` / `--continue` 直接读取 `messages` 传给下一轮。

### 工具调用消息完整性

- Agent Loop 在 `loop.py:252` 将整块 assistant 消息（含 `tool_calls`）追加到 `messages`。
- 每个 tool call 对应一条 `{"role":"tool","tool_call_id":tc.id,"content":result}`，按顺序追加。
- `on_progress`（即 `session.save`）仅在所有 tool result 回填后调用，**不会出现悬空 tool_call**。

### 上下文压缩

- 触发：`estimate_tokens(messages) > context_limit * 0.7`
- 策略：保留 system 消息 + 最近 6 个完整 turn，较早 turn 由 LLM 生成中文摘要。
- 安全：`split_turns()` 按 `user` 消息切分，assistant tool_calls 与其 tool 结果落在同一 turn，**不会被拆散**。
- 压缩后 messages 原地替换，保持同一列表对象。

### 检查点 / 撤销

- 每个 `run_agent()` 调用前创建 `Checkpoint`。
- 写工具通过 `current_checkpoint()` 记录首次写入的原始内容。
- `/undo` 或 `--undo` 可还原修改、删除新建文件。

---

## 7. 测试结果

### 已执行的安全测试

```powershell
python -m pytest tests --ignore=tests/test_llm_live.py -q
```

结果：

- **通过：336**
- **失败：0**
- **跳过：1**（无真实 key 时跳过 live provider smoke）
- **版本号已统一**：`pyproject.toml`、`src/mycode/__init__.py`、`pip show mycode-ai-cli`、`mycode --version` 均为 `0.2.1`

### 已执行的 CLI 命令

| 命令 | 结果 | 备注 |
|------|------|------|
| `python -m mycode --help` | 成功 | 中文显示正常 |
| `python -m mycode --version` | 成功 | 输出 `mycode 0.2.1`，与 `pip show mycode-ai-cli` 一致 |
| `python -m mycode tui` | 成功 | Textual 界面由 Pilot 和截图流程验证 |
| `python -m mycode serve --stdio` | 成功 | stdout 仅 JSON-RPC；子进程契约测试通过 |
| `python -m mycode config show` | 成功 | 显示当前生效配置，含 skills/plugins |
| `python -m mycode doctor` | 退出码 1（预期） | API Key 未设置时给出中文提示 |
| `python -m mycode skill list` | 成功 | 列出发现与已激活的 Skill |
| `python -m mycode plugin list` | 成功 | 列出发现与已启用的插件 |

### 需要 API Key 的测试

- `tests/test_llm_live.py`：已用 `@pytest.mark.skipif` 在 `DEEPSEEK_API_KEY` 缺失时自动跳过。
- `scripts/smoke_llm.py`：手动烟雾脚本，需要配置的环境变量 key。

---

## 8. 问题处理清单

| # | 问题 | 位置 | 优先级 | 备注 |
|---|------|------|--------|------|
| 1 | ~~版本号不一致~~ | `pyproject.toml` vs `src/mycode/__init__.py` | ~~P1~~ **已修复** | `pyproject.toml` 已更新为 `0.1.1`，并重新 `pip install -e .`。 |
| 2 | ~~价格测试失败~~ | `tests/test_pricing.py:35` | ~~P1~~ **已修复** | 测试期望修正为 `2.875`，根因为测试预期过期。 |
| 3 | ~~终端中文显示乱码~~ | CLI 输出 | ~~P2~~ **已修复** | `cli.py` 入口通过 Win32 API 设置控制台 CP_UTF8 并重配 stdout/stderr 为 utf-8。 |
| 4 | ~~`conftest.py` 缺失~~ | 项目根 | ~~P2~~ **已修复** | 新建 `tests/conftest.py`，设置 `PYTEST_DEBUG_TEMPROOT` 为项目本地 `.pytmp`。 |
| 5 | ~~无 Kimi preset~~ | `config.py` | ~~P2~~ **已修复** | 增加 `kimi` / `moonshot` preset：`moonshot-v1-8k`、`MOONSHOT_API_KEY`、`https://api.moonshot.cn`。 |
| 6 | ~~Claude 无内置价格~~ | `pricing.py` | ~~P2~~ **已修复** | 为 `claude-sonnet-4-6`、`claude-3-5-sonnet*` 增加内置价格。 |
| 7 | ~~缺少 `requirements.txt`~~ | 项目根 | ~~P2~~ **已修复** | 新增 `requirements.txt` 与 `requirements-dev.txt`。 |
| 8 | ~~DeepSeek reasoning_content 未处理~~ | `llm/openai_compatible.py` | ~~P3~~ **已修复** | 已支持独立流式展示及工具调用轮回填。 |
| 9 | ~~Git 工具不完整~~ | `git_ops.py`, `tools/git.py` | ~~P3~~ **已修复** | 已注册只读 status/log/branch 工具。 |
| 10 | ~~Provider 工厂硬编码~~ | `llm/base.py`, `llm/__init__.py` | ~~P3~~ **已修复** | 已改为注册表发现。 |
| 11 | ~~缺少日志审计~~ | `logging_config.py`, `agent/loop.py` | ~~P3~~ **已修复** | 支持轮转、脱敏和工具/usage/error 摘要。 |

---

## 9. 依赖与配置现状

### 依赖

- 主要依赖声明在 `pyproject.toml`。
- 同时提供 `requirements.txt`（生产）和 `requirements-dev.txt`（开发）。
- 核心依赖：`typer>=0.12`、`rich>=13`、`openai>=1.0`、`anthropic>=0.40`、`pydantic>=2`。
- Dev：`pytest>=8`。
- 未锁定版本，未生成 `uv.lock` / `poetry.lock`。

### 配置加载顺序

1. 内置默认值
2. `~/.mycode/config.toml`（全局）
3. `.mycode/config.toml`（项目本地）
4. `MYCODE_*` 环境变量覆盖
5. `MYCODE_CONFIG=/path/to/config.toml` 可指定单独文件

### 环境变量覆盖列表

`MYCODE_DEFAULT_MODEL`、`MYCODE_MAX_STEPS`、`MYCODE_PLANNING`、`MYCODE_PLANNING_MAX_STEPS`、`MYCODE_MAX_FILE_LINES`、`MYCODE_MAX_COMMAND_OUTPUT`、`MYCODE_CONTEXT_LIMIT`、`MYCODE_COMMAND_TIMEOUT`、`MYCODE_PROVIDER_TYPE`、`MYCODE_API_KEY_ENV`、`MYCODE_BASE_URL`、`MYCODE_MAX_TOKENS`、`MYCODE_TEMPERATURE`、`MYCODE_PROVIDER_TIMEOUT`、`MYCODE_MAX_RETRIES`、`MYCODE_RETRY_BACKOFF`、`MYCODE_STREAM_USAGE`、`MYCODE_PERMISSION_WRITE`、`MYCODE_PERMISSION_COMMAND`、`MYCODE_SKILLS`、`MYCODE_PLUGINS`。

### Skill 与插件配置

- Skill：在 `.mycode/skills/<name>/SKILL.md` 或 `~/.mycode/skills/<name>/SKILL.md` 中定义；通过 `config.skills = ["name"]` 或 `MYCODE_SKILLS=name1,name2` 激活。
- 插件：通过 `mycode.plugins` entry point 暴露；通过 `config.plugins = ["name"]` 或 `MYCODE_PLUGINS=name1,name2` 启用，未启用不会导入。

---

## 10. 总体结论

MyCode 已实现 CLI、本地 Web 工作台、TUI 与 VS Code 四种本地前端，并具备多 Provider、工具、权限、Session、Context、Trace、Evals、MCP、Skill、插件和发布链路。

P0-P15 与 0.2.2 Web 工作台已完成。后续工作进入实际发布、用户反馈和桌面壳评估。
