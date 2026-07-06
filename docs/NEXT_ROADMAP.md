# MyCode P5-P11 下一阶段路线图

> 状态：P5-P11 已于 2026-06-30 完成。当前发行版本为 `0.2.0`。

## 基线审计

- P1-P4 核心能力已存在，但 docs 目前只明确记录至 P3，实施路线图前需统一阶段编号。
- Agent Loop 与 Rich UI、全局 Tool Registry 直接耦合，缺少稳定事件协议、取消机制和结构化结果。
- 现有测试以单元测试和 FakeProvider 为主，缺少任务级 Evals、基线比较和 CI。
- Session 保存未版本化的原始消息；日志为文本文件，没有 trace_id、span 或结构化导出。
- 审计时尚无 MCP、Skill、插件、TUI、VS Code 和正式发布目录；这些缺口现已按本路线图完成。

## P5：可靠性与 Trace 基础（已完成）

- **状态：** ✅ 已完成。

- **目标 / 用户价值：** 建立可观察、可取消、可复现的运行内核；用户能定位失败步骤、重试原因和工具耗时。
- **当前缺口：** `run_agent` 直接输出 UI，工具错误使用字符串表达，没有稳定事件、超时、取消、trace schema。
- **涉及模块：** Agent Loop、Provider、Tool Registry、Session、日志、配置和权限确认。
- **文件级修改范围：** 重构 `agent/loop.py`、`tools/registry.py`、`logging_config.py`；新增 `agent/events.py`、`agent/runner.py`、`trace.py`。
- **数据结构：** `RunRequest`、`AgentRunResult`、`AgentEvent(schema_version, run_id, seq, type, timestamp, payload)`、`ToolResult`、`CancellationToken`、`TraceRecord`。
- **实施步骤：** 抽离 UI 无关的 `AgentRunner`；保留旧 `run_agent` 兼容适配器；统一 provider/tool/permission/context 事件；实现瞬时错误重试、总截止时间、取消和 JSONL Trace。
- **Trace 策略：** 默认只记录元数据，提示词、工具输入输出必须显式开启；所有记录经过现有脱敏器；提供可选 OpenTelemetry exporter。
- **测试方案：** 注入 FakeProvider、假时钟和确定性 ID；覆盖超时、429/5xx、取消、工具异常、trace 顺序、脱敏和旧接口兼容。
- **验收标准：** 每次运行具有唯一 `run_id`；事件序号稳定；失败有结构化原因；Trace 可重放执行路径；全部测试无网络运行。
- **风险 / 回滚：** 重构可能改变消息顺序；通过兼容适配器和特性开关回退到旧 Loop，Trace 可独立关闭。
- **依赖关系：** 无；是后续全部里程碑的基础。

## P6：Evals 与回归测试（已完成）

- **状态：** ✅ 已完成。

- **目标 / 用户价值：** 用离线任务集衡量 Agent 行为，避免模型、工具和提示词修改造成静默回归。
- **当前缺口：** 现有测试验证函数行为，但没有任务级工作区、评分器、历史基线和 CI 门禁。
- **涉及模块：** AgentRunner、FakeProvider、工具、权限、Context、Session 和 Git 操作。
- **文件级修改范围：** 新增 `src/mycode/evals/`、`evals/cases/`、`evals/baselines/`、`tests/test_evals.py` 和 `.github/workflows/ci.yml`。
- **数据结构：** `EvalCase` 包含 prompt、工作区 fixture、脚本响应、预算和期望；`EvalResult` 包含状态、工具序列、文件差异、trace 和评分；`EvalBaseline` 按 case 保存阈值。
- **实施步骤：** 在临时目录构造任务；使用确定性 Provider；实现 exact、contains、tool-sequence、file-state、permission 和 unexpected-write 评分器；增加 `mycode eval run`。
- **测试方案：** 覆盖读取修改、Shell 失败修复、权限拒绝、上下文压缩、循环检测和恢复 Session；单测评分器及 baseline 比较。
- **验收标准：** 核心 eval 全部离线通过；关键 case 不允许退化；结果可输出机器可读 JSON；CI 不需要 API key。
- **风险 / 回滚：** 脆弱的文本断言会产生误报；核心门禁使用结构和文件状态评分，文本评分仅作辅助；可临时冻结 baseline 但不得自动覆盖。
- **依赖关系：** 依赖 P5；P7-P10 的功能必须同步增加 eval case。

## P7：MCP Client（已完成）

- **状态：** ✅ 已完成。

- **目标 / 用户价值：** 让 MyCode 使用外部 MCP tools、resources 和 prompts，复用现有工具生态。
- **当前缺口：** 工具只能通过进程内同步 Registry 注册，不支持连接生命周期、命名空间和 MCP 内容类型。
- **涉及模块：** Tool Registry、权限、配置、Prompt 构建、AgentRunner 和 CLI。
- **文件级修改范围：** 新增 `src/mycode/mcp/`；修改 `config.py`、`tools/registry.py`、`permissions.py`、`cli.py` 和可选依赖配置。
- **数据结构：** `MCPServerConfig(name, command, args, env, timeout, trusted)`、`MCPToolRef`、`MCPResourceRef`、`MCPPromptRef`、`MCPCallResult`。
- **实施步骤：** 使用官方 Python SDK；v1 仅启用本地 stdio；懒启动并管理 session；以 `mcp__server__name` 命名能力；转换 MCP 内容和错误；支持资源读取与 prompt 展开。
- **安全策略：** Server 默认禁用且不受信任；工具注解仅作提示，不能绕过确认；子进程环境采用显式配置；roots 固定为解析后的项目根目录。
- **测试方案：** 启动测试 MCP stdio server，覆盖发现、调用、资源、prompt、超时、崩溃、重连、重名、非法响应和权限拒绝。
- **验收标准：** 配置后可稳定调用三类 MCP 能力；Server 故障不会终止 Agent；默认不会访问项目外路径；测试不访问公网。
- **风险 / 回滚：** 第三方 Server 可执行任意本机代码；UI 和 README 必须提示其不是沙箱；删除或禁用 server 配置即可完全回滚。
- **依赖关系：** 依赖 P5、P6；本阶段不实现 MyCode MCP Server 和远程 HTTP transport。官方 SDK 依据：[MCP Python SDK](https://py.sdk.modelcontextprotocol.io/client/)。

## P8：Skill 与插件系统（已完成）

- **状态：** ✅ 已完成（基于已落地的 P3 Provider/Tool 注册表实现，不依赖 P5/P6/P7）。
- **目标 / 用户价值：** 支持复用工作流说明和安装第三方能力，同时保持版本、来源和启用状态可追踪。
- **当前缺口：** 现有 commands 只是 Markdown 模板；Provider 和 Tool 使用全局注册表，没有插件 API 或兼容性检查。
- **涉及模块：** Prompt、Commands、Provider Registry、Tool Registry、配置和 CLI。
- **文件级修改范围：** 新增 `src/mycode/skills/`、`src/mycode/plugins/`；修改 `commands.py`、`prompts.py`、`config.py` 和 `cli.py`。
- **数据结构：** `SkillManifest(name, version, description, required_tools)`；`PluginSpec(name, version, api_version)`；`PluginRegistrar` 提供受控的 provider、tool 和 skill 注册接口。
- **实施步骤：** 定义 `.mycode/skills/<name>/SKILL.md` 及 references；v1 只允许显式激活 Skill；通过 `mycode.plugins` entry point 发现 Python 插件；插件必须在配置中显式启用。
- **兼容策略：** Plugin API 使用独立版本；名称冲突直接报错；Commands 保持原行为；Skill 不自动执行附带脚本。
- **测试方案：** 覆盖用户和项目 Skill 优先级、无效 manifest、插件发现、禁用、API 不兼容、注册冲突和插件异常隔离。
- **验收标准：** 可列出、检查、启停 Skill/插件；未启用插件不会导入；插件能力进入统一 Registry、Trace 和 Evals。
- **风险 / 回滚：** 进程内插件拥有宿主权限，必须标记为受信任代码而非沙箱；配置禁用即可回滚。
- **依赖关系：** 依赖 P5、P6；可引用 P7 的 MCP 工具。插件发现采用 PyPA 标准：[Creating and discovering plugins](https://packaging.python.org/guides/creating-and-discovering-plugins/)。
- **实现摘要：**
  - `src/mycode/skills/__init__.py`：解析 `.mycode/skills/<name>/SKILL.md` 的 frontmatter，支持本地覆盖全局，激活后注入 system prompt。
  - `src/mycode/plugins/__init__.py`：通过 `mycode.plugins` entry point 发现插件，仅加载 `config.plugins` 中列出的插件；`PluginRegistrar` 控制 tool/provider/skill 注册。
  - `src/mycode/config.py`：新增 `skills`、`plugins` 字段及 `MYCODE_SKILLS`、`MYCODE_PLUGINS` 环境变量。
  - `src/mycode/cli.py`：新增 `mycode skill list` / `mycode plugin list`，运行前加载已启用插件与已激活 Skill。
  - 新增测试：`tests/test_skills.py`、`tests/test_plugins.py`；当前完整离线套件 `330 passed, 1 skipped`。

## P9：Textual TUI（已完成）

- **状态：** ✅ 已完成。

- **目标 / 用户价值：** 提供适合持续编码工作的全屏交互界面，同时保留现有 CLI。
- **当前缺口：** 当前 UI 依赖同步 `input()` 和 Rich 输出，无法稳定处理流式事件、后台任务、取消和并行面板。
- **涉及模块：** Agent 事件、Session、权限确认、Diff、Context、Skill/MCP 状态和 CLI。
- **文件级修改范围：** 新增 `src/mycode/tui/`；修改 `ui.py`、`cli.py` 和 `pyproject.toml` 的 `tui` optional extra。
- **数据结构：** `TUIState` 保存活动 run/session、消息视图、待确认请求和状态栏数据；状态只投影 AgentEvent，不另建业务真相。
- **实施步骤：** 增加 `mycode tui`；实现对话区、多行输入、Session 列表、工具详情、Diff、权限模态框、Context/费用状态和取消操作。
- **测试方案：** 使用 Textual `run_test()`、Pilot 和快照测试键盘、点击、权限流、流式内容及 80×24/宽屏布局。
- **验收标准：** TUI 与 CLI 得到相同最终消息和工具历史；运行时可取消；窗口缩放不重叠；无 TUI extra 时普通 CLI 正常。
- **风险 / 回滚：** 异步 UI 可能暴露线程与事件顺序问题；TUI 作为独立入口，可移除 extra 或回退 CLI。
- **依赖关系：** 依赖 P5、P6、P8；测试方式依据：[Textual Testing](https://textual.textualize.io/guide/testing/)。
- **实现摘要：** 新增共享 `MyCodeRuntime`、可注入审批上下文及 `mycode tui`；TUI 覆盖会话、流式输出、工具活动、Diff、用量、取消和审批，并通过宽屏与 80×24 Pilot 测试。

## P10：VS Code 插件接口（已完成）

- **状态：** ✅ 已完成。

- **目标 / 用户价值：** 在编辑器内启动 Agent、附加选区、查看工具和 Diff、处理权限确认及恢复 Session。
- **当前缺口：** 没有无 UI 的进程协议；stdout 混有渲染内容；没有取消、握手、能力协商或协议版本。
- **涉及模块：** AgentRunner、Session、权限、Trace、CLI 服务入口和 TypeScript Extension。
- **文件级修改范围：** 新增 `src/mycode/server/`、`schemas/protocol-v1.json` 和 `editors/vscode/`。
- **数据结构：** JSON-RPC 2.0 over stdio；`initialize` 返回版本和 capabilities；支持 `run/start`、`run/cancel`、`permission/respond`、`session/*`，并通过 notification 发送 `AgentEvent`。
- **实施步骤：** 增加 `mycode serve --stdio`；stdout 只传协议、日志写 stderr；扩展运行于 workspace extension host；实现命令、Session TreeView 和必要的对话 Webview。
- **兼容范围：** 支持本地 VS Code、SSH、WSL 和 Dev Container；v1 不支持浏览器 Web Extension。
- **测试方案：** Python 协议单测、JSON Schema 契约测试、TypeScript 单测、子进程集成测试和 `@vscode/test-electron` smoke test。
- **验收标准：** 可从活动工作区完成一次完整 Agent/工具/确认流程；协议错误不导致扩展卡死；取消和进程退出能清理资源。
- **风险 / 回滚：** Python 与 TypeScript 协议漂移；以版本化 Schema 和 golden messages 门禁；扩展可禁用且不影响 CLI。
- **依赖关系：** 依赖 P5、P6；复用 P7-P9 能力。扩展结构依据：[Extension Anatomy](https://code.visualstudio.com/api/get-started/extension-anatomy)、[Extension Hosts](https://code.visualstudio.com/api/advanced-topics/extension-host)。
- **实现摘要：** `mycode serve --stdio` 提供协议 v1；VS Code workspace extension 提供 Session TreeView、React Agent Webview、选区上下文、工具活动、取消和审批。Python 契约、Extension Host 和桌面/窄屏 Playwright 检查通过。

## P11：发布与安装（已完成）

- **状态：** ✅ 已完成。

- **目标 / 用户价值：** 提供可验证、可升级、可回滚的 PyPI/pipx 和 VSIX 安装方式。
- **当前缺口：** 缺少许可证、完整包元数据、兼容矩阵、构建检查、版本流程、CI 和可信发布。
- **涉及模块：** Python 包、可选依赖、CLI 版本、文档、GitHub Actions 和 VS Code 包。
- **文件级修改范围：** 修改 `pyproject.toml`、README；新增 `LICENSE`、`NOTICE`、`CHANGELOG.md`、发布 workflows 和 VS Code 打包配置。
- **数据结构：** PyPI distribution 名为 `mycode-ai-cli`，导入名和命令保持 `mycode`；采用 SemVer 和 `vX.Y.Z` 标签；许可证为 Apache-2.0。
- **实施步骤：** 将 Python 支持范围验证并调整为 3.11-3.14；定义 `mcp`、`tui`、`trace`、`all` extras；构建 wheel/sdist；执行 TestPyPI、pipx 和 VSIX 安装 smoke test；使用 OIDC Trusted Publishing。
- **CI 矩阵：** Ubuntu 覆盖 Python 3.11-3.14，Windows/macOS 覆盖最低和最高版本；执行单测、离线 eval、build、metadata 和 VSIX 检查。
- **测试方案：** 在干净虚拟环境安装 wheel；验证 `mycode --version`、基础离线运行、extras、插件 entry point、卸载和升级。
- **验收标准：** PyPI 包可由 pipx 安装；GitHub Release 附带 wheel、sdist、VSIX、校验值和变更日志；发布过程不保存长期 PyPI token。
- **风险 / 回滚：** 依赖或 Python 版本兼容失败；发布前必须通过 TestPyPI；问题版本执行 yank，发布修复版本，不覆盖已有标签。
- **依赖关系：** 依赖 P5-P10；打包流程依据：[PyPA Packaging](https://packaging.python.org/en/latest/tutorials/packaging-projects/) 和 [Trusted Publishing](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/)。
- **实现摘要：** 发行名 `mycode-ai-cli`、版本 `0.2.0`、Apache-2.0、Python 3.11-3.14；wheel、sdist、VSIX、CI 和 TestPyPI→PyPI OIDC 工作流已落地。离线测试集合为 `330 passed, 1 skipped`。

## 顺序与边界

实施已按 `P5 → P6 → P7 → P8 → P9 → P10 → P11` 完成。P9/P10 共用 P5 事件协议、共享 Runtime 和统一审批接口。

本路线图不包含远程多租户服务、MyCode MCP Server、Streamable HTTP MCP、VS Code Web Extension、独立单文件二进制、插件沙箱或自动 Skill 匹配。命令黑名单、路径限制、MCP 信任和插件启用都只是基础防护；真正隔离仍需容器、受限用户或其他 OS 级机制。
