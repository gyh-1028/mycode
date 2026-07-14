# MyCode 当前架构文档

> 生成时间：2026/07/03  
> 已同步补充 P13/P14 与多模型配置档。

## 最新扩展

- `src/mycode/model_store.py` 管理 `~/.mycode/models.toml` 中的多个模型配置档；Token 通过 `keyring` 进入操作系统凭据库。
- `mycode model ...` 提供预设、添加、切换、修改 Token、编辑和删除能力；环境变量仍具有最高凭据优先级。
- `src/mycode/codeintel/` 提供 SQLite 符号索引、LSP 和临时精准上下文。
- `src/mycode/evals/live_*` 提供真实模型任务 Eval、预算、报告与 A/B 对比。

---

## 1. 架构总览

MyCode 采用**分层架构**：

```
┌─────────────────────────────────────────────┐
│  CLI Layer (typer)                          │  src/mycode/cli.py
│  - 参数解析、REPL、命令拦截、会话恢复         │
├─────────────────────────────────────────────┤
│  Agent Loop                                 │  src/mycode/agent/loop.py
│  - 多步工具调用、流式输出、卡住检测、预算     │
├─────────────────────────────────────────────┤
│  Planning / Context                         │  src/mycode/agent/planning.py
│  - 任务计划生成与注入                         │  src/mycode/context.py
│  - 上下文压缩与摘要                          │
├─────────────────────────────────────────────┤
│  LLM Provider Abstraction                   │  src/mycode/llm/{base,__init__,openai_compatible,anthropic}.py
│  - 归一化类型、工厂、OpenAI/DeepSeek/Claude   │
├─────────────────────────────────────────────┤
│  Tool Registry + Tools                      │  src/mycode/tools/{registry,files,search,shell}.py
│  - 工具注册、文件读写改、搜索、Shell          │
├─────────────────────────────────────────────┤
│  Permissions / Guardrails                   │  src/mycode/permissions.py
│  - 路径边界、敏感文件、命令黑名单             │
├─────────────────────────────────────────────┤
│  Persistence                                │  src/mycode/session.py, checkpoint.py
│  - 会话存档、检查点与撤销                     │
├─────────────────────────────────────────────┤
│  Config / Prompts / UI / Utils              │  src/mycode/config.py, prompts.py, ui.py, pricing.py, mentions.py, git_ops.py, logging_config.py
└─────────────────────────────────────────────┘
```

---

## 2. 入口与启动流程

### 2.1 入口点

| 入口 | 文件 | 说明 |
|------|------|------|
| `mycode` 脚本 | `pyproject.toml:[project.scripts]` | `mycode = "mycode.cli:app"` |
| `python -m mycode` | `src/mycode/__main__.py` | 导入 `app()` 并执行 |
| 核心命令 | `src/mycode/cli.py:main()` | Typer 命令函数 |

### 2.2 启动分支

`main()` 在 `src/mycode/cli.py:476-493` 中按以下优先级处理：

1. `--sessions` 或 `task == "sessions"` → 列会话
2. `task == "init"` → 生成配置
3. `task == "doctor"` → 诊断配置/API key
4. `task == "config"` → 配置子命令
5. `task == "session"` → 会话子命令
6. `--undo` → 撤销

否则进入正常任务/REPL 流程：

```text
load_config_result()              # 读取配置
  ↓
config.provider.resolve_api_key() # 从环境变量读 key
  ↓
build_provider(config, api_key)   # 创建 LLM provider
  ↓
Session.load(resume) / Session.latest() / Session.new()  # 恢复或新建会话
  ↓
构建 messages（system prompt + 历史）
  ↓
Checkpoint.begin() + set_current_checkpoint()
  ↓
run_agent(provider, messages, ...)
  ↓
session.save(messages)            # 最终存档
  ↓
auto_commit_checkpoint()（若 --commit）
```

---

## 3. 完整调用链追踪

### 3.1 用户输入 → CLI

**文件**：`src/mycode/cli.py:453`

```python
@app.command(...)
def main(
    ctx: typer.Context,
    task: str | None = None,
    resume: str | None = None,
    continue_: bool = False,
    ...
)
```

- `task` 是可选位置参数。
- 若 `task` 为关键字命令，直接拦截处理。
- 若 `task` 不为 None，进入 one-shot 模式；否则进入 `_run_repl()`。

### 3.2 CLI → Agent Loop

**文件**：`src/mycode/cli.py:551`

```python
run_agent(
    provider,
    messages,
    max_steps=config.max_steps,
    on_progress=_save,
    context_limit=config.context_limit,
    planning=config.planning,
    planning_max_steps=config.planning_max_steps,
    budget_usd=budget,
    model=config.default_model,
    pricing_overrides=config.pricing,
)
```

- `messages` 是 OpenAI 格式的 dict 列表。
- `_save` 是闭包：`session.save(messages)`。

### 3.3 Agent Loop 内部

**文件**：`src/mycode/agent/loop.py:127`

`run_agent()` 主循环逻辑：

```text
初始化工具 schemas、计划状态、用量统计、卡住检测状态
  ↓
for _step in range(max_steps):
    maybe_compact(provider, messages, context_limit)  # 上下文压缩
      ↓
    model_messages = inject_plan_context(messages, plan_state, validation_required)
      ↓
    response = _consume_stream(provider.stream(model_messages, schemas), out)
      ↓
    累加 usage
      ↓
    messages.append(_assistant_message(response))  # assistant 整块
      ↓
    if response.tool_calls:
        for tc in response.tool_calls:
            result = dispatch(tc.name, tc.args)      # 执行工具
            messages.append({"role":"tool", ...})    # 回填结果
            更新卡住检测 / 计划进度
        _checkpoint()  # on_progress → session.save
        检查 stuck / budget
        continue
    else:
        # 最终回答
        _checkpoint()
        _emit_usage()
        return response.text
```

### 3.4 Agent Loop → LLM Provider

**文件**：`src/mycode/llm/__init__.py:18`

```python
def build_provider(config: Config, api_key: str) -> BaseProvider:
    cls = get_provider_class(config.provider.type)
    if cls is None:
        cls = OpenAICompatibleProvider  # 兼容未知 OpenAI-compatible 类型
    return cls(...)
```

**`BaseProvider.stream()`**：`src/mycode/llm/base.py:73`
- 抽象接口：返回生成器，yield 文本块，最终返回 `LLMResponse`。

**`OpenAICompatibleProvider.stream()`**：`src/mycode/llm/openai_compatible.py:182`
- 调用 `client.chat.completions.create(..., stream=True, stream_options={"include_usage": True})`
- 失败时自动降级（去掉 `stream_options`）。
- 累积文本、`reasoning_content` 与 tool call JSON 片段；reasoning 通过类型化 `ReasoningChunk` 独立输出。
- 流结束后调用 `_assemble_tool_calls()` 解析参数。

**`AnthropicProvider.stream()`**：`src/mycode/llm/anthropic.py:221`
- 将 OpenAI 格式消息转换为 Anthropic 格式。
- 使用 `client.messages.stream()`。
- 返回归一化的 `LLMResponse`。

### 3.5 LLM Provider → Tool Call

**文件**：`src/mycode/agent/loop.py:254-264`

```python
for tc in response.tool_calls:
    result = dispatch(tc.name, tc.args)
    messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
```

### 3.6 Tool Call → Tool Execution

**文件**：`src/mycode/tools/registry.py:55`

```python
def dispatch(name: str, args: Any) -> str:
    tool = _REGISTRY[name]
    return tool.func(**args)
```

具体工具：

| 工具 | 文件 | 功能 |
|------|------|------|
| `list_files` | `tools/files.py:54` | 列目录，支持 `.mycodeignore` |
| `read_file` | `tools/files.py:98` | 读文件，支持行范围、截断 |
| `edit_file` | `tools/files.py:193` | 唯一替换，落盘前确认 |
| `write_file` | `tools/files.py:241` | 写入/覆盖，落盘前确认 |
| `apply_patch` | `tools/files.py:331` | 应用 unified diff |
| `search_code` | `tools/search.py:105` | ripgrep / Python fallback |
| `find_files` | `tools/search.py:132` | glob 查找 |
| `run_bash` | `tools/shell.py:64` | 执行 shell，需确认 |
| `git_status` | `tools/git.py` | 只读查看工作区状态 |
| `git_log` | `tools/git.py` | 只读查看最近提交 |
| `git_branch` | `tools/git.py` | 只读查看当前/本地分支 |

### 3.7 Tool Result → 文件修改 / 命令执行

写工具统一调用 `_confirm_and_write()`（`tools/files.py:150`）：

```text
生成 unified diff
  ↓
检查 permissions.write
  ↓
ask 模式：request_write_approval() 显示 diff 并 y/N 确认
  ↓
record_file_write(target, original, existed)  # 记录 checkpoint
  ↓
Path.write_text(new_content)
```

Shell 工具：

```text
command_denial_reason() / command_path_denial_reason()  # 黑名单
  ↓
classify_command_risk()  # 风险分级
  ↓
request_command_approval()  # 确认
  ↓
subprocess.run(command, shell=True, timeout=...)
  ↓
返回 "[退出码 N]\n{output}"
```

### 3.8 最终输出

- 流式文本已在 `_consume_stream()` 中边收边打印。
- 推理内容以灰色“思考:”前缀独立输出，不混入最终答案；工具调用轮保留独立字段供 DeepSeek 后续请求使用。
- 工具调用结果以 `"  ↳ 返回 N 字符"` 形式显示。
- 最终答案文本由 `run_agent()` 返回，`main()` 不再额外打印。
- 轮末调用 `_emit_usage()` 打印累计 token 与估算成本。

---

## 4. 数据流与消息格式

### 4.1 内部 Messages 格式（OpenAI 形状）

```python
# System
{"role": "system", "content": "..."}

# User
{"role": "user", "content": "task"}

# Assistant with tool calls
{
    "role": "assistant",
    "content": "optional reasoning",
    "tool_calls": [
        {
            "id": "call_xxx",
            "type": "function",
            "function": {
                "name": "edit_file",
                "arguments": '{"path":"...","old_str":"...","new_str":"..."}'
            }
        }
    ]
}

# Tool result
{"role": "tool", "tool_call_id": "call_xxx", "content": "result string"}
```

### 4.2 Tool Schema 内部格式

```python
{
    "name": "edit_file",
    "description": "...",
    "parameters": {
        "type": "object",
        "properties": {...},
        "required": [...]
    }
}
```

转换为 OpenAI 格式：`_to_openai_tool()`。  
转换为 Anthropic 格式：`_to_anthropic_tools()`。

### 4.3 Provider 归一化输出

```python
class LLMResponse(BaseModel):
    text: str | None
    reasoning_content: str | None
    tool_calls: list[ToolCall] | None
    stop_reason: StopReason
    usage: Usage

class ToolCall(BaseModel):
    id: str
    name: str
    args: dict[str, Any]

class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cached_tokens: int = 0
    cache_write_tokens: int = 0

@dataclass(frozen=True)
class ReasoningChunk:
    content: str
```

---

## 5. 关键子系统详解

### 5.1 上下文压缩

**文件**：`src/mycode/context.py:104`

```text
estimate_tokens(messages)  # 字符数/4 + 开销
  ↓
若 > context_limit * 0.7:
    split_turns(messages)  # system + turns
      ↓
    保留最近 6 个 turn
    较早 turn 通过 _summarize_with_llm() 生成中文摘要
      ↓
    messages[:] = [system..., summary_msg, recent turns...]
```

**不变量**：只按完整 turn 丢弃，tool_calls 与 tool 结果成对保留。

### 5.2 检查点与撤销

**文件**：`src/mycode/checkpoint.py`

- `Checkpoint.begin()`：每个 `run_agent()` 调用前创建。
- `record_write(target, original, existed)`：写工具首次写入时调用，记录原始内容。
- `undo()`：恢复修改、删除新建文件，标记 `undone_at`。
- 通过 `ContextVar` `_CURRENT` 在当前任务上下文中传递。

### 5.3 会话持久化

**文件**：`src/mycode/session.py:69`

```python
def save(self, messages):
    self.messages = messages
    self.updated_at = _now_iso()
    tmp = self.base_dir / f"{self.id}.json.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    os.replace(tmp, self.path)
```

- ID 格式：`YYYYMMDD-HHMMSS-<uuid4[:4]>`
- 保存目录：`.mycode/sessions/`
- 原子写入，避免半截文件。

### 5.4 权限系统

**文件**：`src/mycode/permissions.py`

| 功能 | 函数 | 说明 |
|------|------|------|
| 项目根解析 | `project_root()` | `Path.cwd()` |
| 路径边界 | `check_project_path()` | 必须位于项目根内 |
| 读权限 | `check_read_path()` | 拒绝敏感文件 |
| 写权限 | `check_write_path()` | 拒绝敏感文件 |
| 敏感路径 | `is_sensitive_path()` | `.env`、`.ssh`、`*secret*`、`.pem`、`.key` 等 |
| 命令黑名单 | `command_denial_reason()` | `rm -rf /`、`curl \| sh`、`mkfs` 等 |
| 命令路径检查 | `command_path_denial_reason()` | 文件操作命令路径边界 |
| 风险分级 | `classify_command_risk()` | `dangerous`/`read`/`write`/`unknown` |

### 5.5 计划系统

**文件**：`src/mycode/agent/planning.py`

- `should_plan_task()`：`auto` 模式下根据任务长度/关键词决定是否生成计划。
- `create_task_plan()`：单独调用 provider.chat() 生成步骤列表。
- `PlanState`：跟踪每步 `pending/in_progress/done/skipped`。
- `inject_plan_context()`：在每轮发送前将计划状态作为临时 system 消息注入。
- 计划状态**不写入**持久化 session messages。

### 5.6 Provider 适配

#### OpenAI 兼容层

**文件**：`src/mycode/llm/openai_compatible.py`

- 支持 OpenAI 与 DeepSeek。
- `_parse_usage()` 同时处理：
  - DeepSeek `prompt_cache_hit_tokens`
  - OpenAI `prompt_tokens_details.cached_tokens`
- `stream()` 默认请求 `stream_options={"include_usage": True}`，失败自动降级。
- 非流式读取 `message.reasoning_content`；流式读取 `delta.reasoning_content`。

#### Provider 注册表

- `@register_provider("name")` 将 `BaseProvider` 子类注册到发现表。
- `build_provider()` 支持无参、显式标准参数和 `**kwargs` 构造器。
- 未知 provider 类型继续回退到 `OpenAICompatibleProvider`，保持旧配置兼容。

#### Anthropic 适配层

**文件**：`src/mycode/llm/anthropic.py`

- `_to_anthropic_messages()`：
  - 提取 system 为顶层参数
  - 合并连续 tool 结果为单条 user 消息
  - assistant `tool_calls` → `tool_use` blocks
- `_build_kwargs()`：
  - 在 system prompt 和最后一个 tool schema 加 `cache_control: {"type": "ephemeral"}`
- `_normalize()`：将 Anthropic `Message` 转回 `LLMResponse`。

---

## 6. 文件职责索引

| 文件 | 职责 |
|------|------|
| `src/mycode/__main__.py` | `python -m mycode` 入口 |
| `src/mycode/cli.py` | 参数解析、REPL、关键字命令、会话恢复、检查点设置 |
| `src/mycode/commands.py` | 自定义斜杠命令模板加载 |
| `src/mycode/config.py` | 配置模型、分层加载、环境变量覆盖、preset |
| `src/mycode/context.py` | Token 估算、上下文压缩、中文摘要 |
| `src/mycode/session.py` | 会话 JSON 保存/加载/列表 |
| `src/mycode/checkpoint.py` | 任务级写入检查点与撤销 |
| `src/mycode/permissions.py` | 路径/命令权限、黑名单、风险分级 |
| `src/mycode/prompts.py` | 系统提示、MYCODE.md 注入 |
| `src/mycode/ui.py` | Diff、确认、流式输出、用量/成本显示 |
| `src/mycode/pricing.py` | Token 成本估算 |
| `src/mycode/mentions.py` | `@path` 上下文注入 |
| `src/mycode/git_ops.py` | 自动提交与 diff 渲染 |
| `src/mycode/logging_config.py` | 按日轮转文件日志、级别配置和凭据脱敏 |
| `src/mycode/agent/loop.py` | Agent 主循环 |
| `src/mycode/agent/planning.py` | 任务计划生成与进度注入 |
| `src/mycode/llm/base.py` | Provider 抽象与归一化类型 |
| `src/mycode/llm/__init__.py` | Provider 工厂 |
| `src/mycode/llm/openai_compatible.py` | OpenAI / DeepSeek 实现 |
| `src/mycode/llm/anthropic.py` | Claude 实现 |
| `src/mycode/tools/registry.py` | Tool 注册表与 dispatch |
| `src/mycode/tools/files.py` | 文件工具 |
| `src/mycode/tools/search.py` | 搜索工具 |
| `src/mycode/tools/shell.py` | Shell 工具 |
| `src/mycode/tools/git.py` | 只读 Git 状态、日志与分支工具 |
| `src/mycode/tools/_common.py` | 工具公共函数 |

---

## 7. 交互与外部依赖

### 外部命令依赖

| 命令 | 用途 | 是否必需 |
|------|------|----------|
| `git` | `--commit` 自动提交、`/diff` | 否（ gracefully 跳过） |
| `rg` / `ripgrep` | `search_code` 优先使用 | 否（Python fallback） |

### 网络依赖

- 运行时需要访问配置的 LLM endpoint（DeepSeek / OpenAI / Anthropic）。
- `mycode doctor` 默认不联网；`doctor --api` 才真实调用 API。

---

## 8. 关键设计决策

1. **OpenAI 格式作为内部 lingua franca**：所有上层逻辑使用 OpenAI 消息格式，Provider 层负责转换。降低多 Provider 复杂度。
2. **流式优先**：默认使用 `provider.stream()`，文本增量即时打印，改善用户体验。
3. **一致性点存档**：`on_progress` 仅在 assistant + 所有 tool results 回填后调用，保证 session 文件始终 API-valid。
4. **整轮压缩**：上下文压缩以 turn 为单位，避免 orphan tool_call。
5. **检查点而非无限 undo**：每个 `run_agent()` 调用是一个检查点，`--undo` 只撤销最近一次任务写入。
6. **权限是“减速带”而非沙箱**：黑名单和路径检查减少误操作，但不提供 OS 级隔离。
7. **Reasoning 与最终答案分离**：思考链使用独立流事件/字段展示，不拼入 assistant content。
8. **可扩展 Provider 注册表**：新增实现通过注册完成，工厂不再增加硬编码分支。
9. **脱敏审计**：DEBUG 日志只记录工具元数据、usage 与错误摘要，并在格式化阶段统一脱敏凭据。

---

## 9. 调用链速查

```text
用户输入
  → mycode.cli:main()
    → load_config_result() / resolve_api_key() / build_provider()
    → Session.new/load/latest()
    → Checkpoint.begin()
    → run_agent()
      → maybe_compact()
      → inject_plan_context()
      → provider.stream(model_messages, schemas)
        → OpenAICompatibleProvider / AnthropicProvider
      → _consume_stream()
      → messages.append(assistant)
      → for tc in response.tool_calls:
           dispatch(tc.name, tc.args)
           messages.append(tool result)
      → on_progress() → session.save()
    → auto_commit_checkpoint() (optional)
```
## 10. 0.2.0 前端与发布架构

MyCode 现在有四个入口，但共享同一运行内核：

```text
CLI (Rich) ───────┐
Textual TUI ──────┼─> MyCodeRuntime ─> AgentRunner ─> Provider / Tools / MCP
VS Code Webview ─> JSON-RPC stdio ────┤
Local Web UI ────> JSON-RPC WebSocket ┘
                         │
                         ├─ agent/event notifications
                         ├─ permission/request + permission/respond
                         └─ session/* + run/start + run/cancel
```

- `approvals.py` 使用执行上下文注入前端审批后端；没有注入时保持 Typer `y/N` 行为。
- `runtime.py` 统一创建/恢复 Session、Checkpoint、MCP 生命周期、AgentRunner 和一致状态存档。
- `tui/` 只投影 `AgentEvent`，不建立第二份业务状态。
- `server/` 实现 line-delimited JSON-RPC 2.0 协议 v1，stdout 不承载日志或普通 UI 文本。
- `server.RpcSession` 是 transport-neutral 协议核心；它负责运行中会话隔离、精确到操作的本轮审批缓存和 Checkpoint Undo；`web/` 只监听 `127.0.0.1` 并提供令牌认证的 WebSocket transport。
- `clients/web/` 是 React/Vite 工作台，提供只读工作区浏览、文件/选区上下文、结构化活动/Diff/上下文检查器、模型凭据管理和审批。
- `editors/vscode/` 运行在 workspace extension host；Python 服务 cwd 即受权限层约束的项目根目录。
- `pyproject.toml` 发行名为 `mycode-ai-cli`，通过 extras 分离 `tui`、`mcp`、`trace`；CI 覆盖 Python 3.11-3.14 和 VSIX。

## 11. 0.2.2 可靠性加固

### 持久化协议

- `persistence.py` 提供跨进程 advisory lock、唯一临时文件、`fsync` 和原子替换。
- Session 与模型配置在加载时保存原始字节指纹；保存前发现磁盘已变化会抛出冲突错误，
  不再静默覆盖另一进程的修改。
- Checkpoint 写入使用同一持久化原语；Undo 在执行时重新校验每个路径，防止清单被篡改后越界恢复。

### Runtime 生命周期

- `MyCodeRuntime` 在生命周期内复用 MCP Registry 与代码智能服务，不再每轮重复启动进程。
- CLI、TUI、RPC 关闭和模型切换均释放旧 Runtime；`close()` 幂等。
- SQLite 连接由索引事务作用域持有并在退出时关闭。

### 模型目录

- `data/model_catalog-v1.json` 是模型、思考能力和价格的单一版本化数据源。
- `catalog.py` 校验 schema、默认模型、显示文本和价格结构。
- `model/list` 返回目录版本与验证时间；`doctor` 显示当前模型价格已知/未知。
- 未复核的数据保持空验证时间，真实 Eval 不会在未知价格下静默绕过预算。

### 代码索引热路径

- 普通增量构建使用内容哈希和批量 SQLite 写入。
- Git 仓库保存上次 HEAD，并通过变更集定位新增、修改和删除文件。
- 大型 Git 仓库可通过 `git cat-file --batch` 读取未修改 tracked blob，减少 Windows 小文件打开成本。
- Agent 写工具完成后直接将变更路径传给 `ContextSelector.invalidate()`，下一次选择只更新相关文件。

### 前端与发布门禁

- Web 完全信任不会持久化；会话和模型边界自动恢复标准确认。
- URL fragment 中的启动令牌会交换到标签页 `sessionStorage`，刷新可自动重连，关闭标签页后清除。
- 运行期间锁定 Session；终态事件触发 Session、Diff、文件树和已打开变更文件的同步刷新。
- Playwright 除截图外检查主区域重叠、横向溢出和无可访问名称的图标按钮。
- CI 只在一个质量 job 运行全量覆盖率与离线 Eval；跨平台矩阵执行持久化、权限和 Runtime smoke。
- Python 构建依赖 Web job，并校验提交的静态资源与 Vite 构建一致；release 同样重新构建 Web。
