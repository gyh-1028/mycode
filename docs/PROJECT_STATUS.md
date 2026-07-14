# MyCode 项目状态

> 更新日期：2026-07-14
>
> 当前版本：`0.2.2`（Alpha）
>
> 阶段：P0-P15 功能已落地，当前进入可靠性与发布前验证阶段。

## 产品形态

MyCode 是本地优先的 AI 编程 Agent。CLI、Textual TUI、本地 Web 工作台和 VS Code
扩展共享 `MyCodeRuntime`、Agent 事件、Session、权限和工具协议。

| 入口 | 状态 | 说明 |
| --- | --- | --- |
| `mycode "<task>"` / REPL | 可用 | 单次任务、多轮会话、恢复、撤销和自动提交 |
| `mycode web` | 可用 | 本机令牌认证、刷新重连、响应式 React 工作台 |
| `mycode tui` | 可用 | Textual 全屏界面 |
| `mycode serve --stdio` | 可用 | JSON-RPC v1，供 VS Code 使用 |

## 已落地能力

| 能力 | 状态 | 关键实现 |
| --- | --- | --- |
| 多 Provider 与模型配置档 | 已完成 | OpenAI compatible、Anthropic、Kimi；Keyring 持久化 |
| Agent Runner 与 Trace | 已完成 | 结构化事件、取消、重试、预算、JSONL Trace |
| 文件、搜索、Shell、Git 工具 | 已完成 | 统一 Registry、Diff 审批、Checkpoint/Undo |
| 权限层 | 已完成 | 项目根与 symlink 校验、敏感文件规则、危险命令拦截 |
| Session 与持久化 | 已完成 | 版本化消息、跨进程锁、冲突检测、原子替换和 fsync |
| MCP、Skill 与插件 | 已完成 | 默认关闭，显式配置启用，统一进入权限和 Trace |
| 离线 Eval | 已完成 | FakeProvider、结构化评分器、基线比较，无网络运行 |
| 真实模型 Eval 框架 | 已完成 | 三个套件、预算、安全档案、报告和 A/B 对比 |
| 代码智能 | 已完成 | SQLite、Python AST、LSP、符号工具、精准上下文 |
| Web 工作台 | 已完成 | 会话隔离、文件上下文、结构化检查器、撤销、审批和模型管理 |
| VS Code 接口 | 已完成 | JSON-RPC stdio、Session TreeView、Agent Webview |
| CI 与发布流程 | 已配置 | 跨平台 smoke、全量质量门禁、Web/VSIX、TestPyPI/OIDC |

## 本轮可靠性加固

- Checkpoint 恢复时再次校验项目根、symlink 和敏感路径，拒绝被篡改的清单。
- Web 的“完全信任”不写入持久存储；新会话、切换会话和切换模型后恢复标准确认。
- Web 刷新使用标签页级临时令牌自动重连；运行中禁止切换会话，完成后刷新受影响文件。
- 审批支持仅本次和本轮精确操作记忆；Diff 检查器支持最近 Checkpoint 撤销。
- Session、模型配置和 Checkpoint 使用跨进程锁、唯一临时文件、原子替换与冲突检测。
- `MyCodeRuntime` 复用 MCP 和代码智能资源，并在 CLI、TUI、RPC 退出或模型切换时关闭。
- SQLite 连接按事务作用域关闭；索引写入批处理，并增加 Git 变更集与大仓库 blob 快速路径。
- 模型目录迁移到版本化 JSON，模型和价格分别暴露验证状态，未知价格不伪造估算。
- CI 将跨平台兼容 smoke 与单次全量质量门禁分开，发布前强制验证 Web 构建产物。

## 当前验证结果

验证环境：Windows，Python 3.14.4，Node.js 24（项目 CI 使用 Node.js 22）。

| 检查 | 结果 |
| --- | --- |
| Ruff | 通过 |
| Pyright | `0 errors` |
| Python 全量测试 | `429 passed, 1 skipped` |
| Python 覆盖率 | `80.58%` |
| 离线 Eval | `6/6` 通过，`0` regression |
| Web TypeScript | 通过 |
| Web Vitest | `16/16` 通过 |
| Web Playwright | 11 张关键截图，3 种尺寸，刷新、布局与可访问性断言通过 |
| VS Code | TypeScript 通过，`2/2` 测试通过，构建通过 |

唯一 Python 警告来自 FastAPI/Starlette TestClient 对 `httpx` 的依赖弃用提示，不影响当前行为。

## 尚未完成的外部验证

1. 尚未产生 `safe-core-v1` 和 `codeintel-v1` 的真实模型三次重复基线。
2. 非 Git 10,000 小文件冷索引仍高于 30 秒目标；100 文件增量目标已达到。
3. 模型 ID、能力和价格目录尚未完成发布前官方复核，`verifiedAt` 仍为空。
4. GitHub Actions、TestPyPI、PyPI Trusted Publishing 和 VSIX 发布需要在远端仓库实际运行验证。

以上项目是发布门禁，不应在 README 或 Release Note 中描述为已经通过。

## 关键目录

```text
src/mycode/agent/          Agent Runner、事件和计划
src/mycode/codeintel/      SQLite 索引、LSP、上下文选择
src/mycode/evals/          离线与真实模型 Eval
src/mycode/server/         transport-neutral JSON-RPC Session
src/mycode/web/            FastAPI/WebSocket 与打包静态资源
clients/web/               React/Vite Web 工作台
editors/vscode/            VS Code 扩展
src/mycode/data/           版本化模型目录
tests/                     离线测试与 FakeRuntime
```

## 安全边界

路径限制、命令黑名单、审批策略、本机监听和临时令牌只是基础防护，不是安全沙箱。
MCP Server 和 Python 插件可能拥有宿主权限。真正隔离需要容器、受限用户、虚拟机或其他
OS 级手段。
