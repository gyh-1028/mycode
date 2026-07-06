# MyCode 本地 Web 工作台

## 启动

```bash
pip install -e ".[web]"
mycode web
```

命令必须在目标项目根目录运行。Web 服务固定绑定 `127.0.0.1`；默认选择随机端口并
打开浏览器，也可以使用 `--port` 和 `--no-open`。

## 架构

```text
React/Vite
  └─ authenticated WebSocket JSON-RPC
       └─ RpcSession
            ├─ MyCodeRuntime / AgentRunner
            ├─ Session / Checkpoint
            ├─ workspace read service
            └─ ModelStore / OS keyring
```

stdio 与 WebSocket 共用 `RpcSession`，因此 CLI、VS Code 和 Web 工作台使用相同的
Agent 事件、取消和权限确认语义。浏览器断开时服务会取消活动运行并拒绝待处理审批。

## 工作与权限模式

- `执行`：正常运行 Agent，并按权限档案调用读取、写入、Shell 和外部工具。
- `计划`：只生成计划，计划形成后立即结束，不调用写入、Shell、MCP 或插件工具。
- `审查`：只向模型暴露读取、搜索、代码诊断和 Git Diff 工具，先输出按严重程度排列的问题。
- `标准确认`：写入、命令和外部工具沿用项目配置的确认策略。
- `只读`：隐藏并拒绝所有写入、Shell 和 MCP 能力。
- `完全信任`：跳过每次操作确认，适合用户明确理解并信任的本地任务。

审批请求使用独立的阻塞式对话框展示命令或 Diff，不再混在活动时间线中。完全信任只改变
确认策略，项目根、symlink、敏感文件和危险命令检查仍然生效。它们都不是安全沙箱；真正
隔离仍需容器、受限用户或其他 OS 级机制。

## 安全边界

- 启动时生成 256 位随机令牌；令牌通过 URL fragment 交给前端并立即从地址栏删除。
- WebSocket 校验令牌和精确 Origin，且只允许一个活动客户端。
- 文件树、预览和搜索继续调用项目路径、symlink 和敏感文件检查。
- API Key 只写入 OS keyring；协议只返回配置状态，不返回密钥内容。
- Web 文件面板只读；修改必须由 Agent 工具产生并经过现有权限策略。

这些措施不是安全沙箱。需要强隔离时仍应使用受限用户、容器或虚拟机。

## 模型配置

设置面板按“服务商、模型、思考模式”保存独立配置档。同一服务商可以保存多个配置，
例如 DeepSeek V4 Flash 快速模式和 V4 Pro 思考模式，并在空闲时切换。当前内置目录包括：

- DeepSeek V4 Flash、V4 Pro，以及弃用前的 `deepseek-chat` / `deepseek-reasoner` 兼容名。
- 智谱 GLM-5.2、GLM-5.1、GLM-5、GLM-5 Turbo、GLM-4.7 系列和 GLM-4.6。
- OpenAI GPT-5.5 / 5.4 系列、Claude Opus 4.8 / Sonnet 5 / Sonnet 4.6。
- Gemini 3.5 Flash / 3.1 Pro、Qwen3.7 / Qwen3 Coder、MiniMax M2.7 / M2.5。
- Kimi Coding Plan 与 Kimi 开放平台 API 两个独立渠道，以及自定义 OpenAI 兼容端点。

思考参数按渠道转换：GPT/Gemini 使用 `reasoning_effort`，Claude 使用 adaptive thinking，
千问使用 `enable_thinking` 与 `thinking_budget`，DeepSeek/GLM 使用 `thinking.type`。
MiniMax M2 系列显示为固定推理模型。

Kimi Coding Plan 使用 `api.kimi.com/coding/v1`、固定模型名 `kimi-for-coding` 和会员额度，
当前推理等级为 `low`/`high`，Thinking 固定开启；Kimi 开放平台使用
`api.moonshot.cn/v1`、K2.7 Code/K2.6/K2.5 等模型并按量计费。K2.7 Code 固定使用
Thinking。两者的密钥不通用。
API Key 仍仅保存到 OS keyring，模型配置文件中不含明文密钥。

## 前端开发

```bash
cd clients/web
npm ci
npm run check
npm test
npm run build
```

生产资源构建到 `src/mycode/web/static/` 并随 wheel 一起发布。发布前必须提交已构建
资源，使从 sdist 安装的用户不需要 Node.js。
