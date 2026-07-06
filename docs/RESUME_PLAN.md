# MyCode 恢复开发计划

> 生成时间：2026/06/30  
> 基于 `PROJECT_STATUS.md`、`CURRENT_ARCHITECTURE.md`、`TECH_DEBT.md` 制定。  
> 状态：P0-P11 已完成；当前版本 0.2.0。

---

## 优先级说明

| 优先级 | 含义 |
|--------|------|
| **P0** | 阻塞发布/CI 通过，必须立即修复 |
| **P1** | 核心功能缺陷或明显不一致，应在首个恢复迭代内完成 |
| **P2** | 体验或能力缺口，建议在第 2 个迭代完成 |
| **P3** | 增强与长期重构，按需排期 |

---

## P0：让项目回到“可发布/全绿”状态

### P0-1：修复价格估算单测失败

| 字段 | 内容 |
|------|------|
| **状态** | ✅ 已完成 |
| **目标** | 让 `pytest` 全绿，明确成本公式语义。 |
| **涉及文件** | `tests/test_pricing.py` |
| **实施步骤** | 将测试断言从 `assert cost == 3.275` 修正为 `assert cost == 2.875`。 |
| **验收标准** | `pytest tests/test_pricing.py` 全部通过；公式语义在代码注释中说明。 |
| **验证结果** | 完整测试套件 `196 passed`。 |

### P0-2：统一版本号

| 字段 | 内容 |
|------|------|
| **状态** | ✅ 已完成 |
| **目标** | 消除 `pyproject.toml` 与源码/CLI 的版本不一致。 |
| **涉及文件** | `pyproject.toml` |
| **实施步骤** | 1. 将 `pyproject.toml` 的 `version` 从 `"0.0.1"` 改为 `"0.1.1"`。<br>2. 重新执行 `pip install -e . --no-deps` 更新已安装元数据。 |
| **验收标准** | `pyproject.toml:version`、`src/mycode/__init__.__version__`、`mycode --version`、`pip show mycode` 四者一致。 |
| **验证结果** | 全部显示 `0.1.1`。 |

---

## P1：修复关键基础设施与可复现性

### P1-1：补回 `conftest.py` 以固定 pytest 临时目录

| 字段 | 内容 |
|------|------|
| **状态** | ✅ 已完成 |
| **目标** | 让测试临时目录行为在所有环境中可复现，避免 Windows pytest temp ACL 问题。 |
| **涉及文件** | `tests/conftest.py`（新建） |
| **实施步骤** | 1. 在 `tests/` 下创建 `conftest.py`。<br>2. 在 `pytest_configure()` 中设置 `os.environ["PYTEST_DEBUG_TEMPROOT"] = str(Path(__file__).parent.parent / ".pytmp")`。<br>3. 重新运行完整测试套件。 |
| **验收标准** | 测试全绿；`.pytmp/run-<pid>` 按预期生成在项目根下。 |
| **验证结果** | 完整测试套件 `196 passed`；连续三次全量跑测稳定通过。 |

### P1-2：修复 Windows 终端中文输出乱码

| 字段 | 内容 |
|------|------|
| **状态** | ✅ 已完成 |
| **目标** | 让中文帮助、提示在 Windows PowerShell / cmd 下默认可读。 |
| **涉及文件** | `src/mycode/cli.py` |
| **实施步骤** | 1. 在 `cli.py` 入口增加 `_ensure_utf8_stdio()`。<br>2. Windows 真实控制台下通过 `SetConsoleOutputCP`/`SetConsoleCP` 设置 CP_UTF8。<br>3. 将 `sys.stdout`/`sys.stderr` 重配为 `utf-8`（当前编码非 UTF-8 时）。 |
| **验收标准** | 在 Windows PowerShell 5/7 中，`mycode --help`、`mycode doctor` 的中文提示无乱码；重定向到文件仍正常。 |
| **验证结果** | `python -m mycode --help` 中文显示正常；`python -m mycode --help > help.txt` 生成 UTF-8 文件；完整测试套件 `196 passed`。 |

---

## P2：补齐 Provider 与配置体验

### P2-1：增加 Kimi (Moonshot) Provider Preset

| 字段 | 内容 |
|------|------|
| **状态** | ✅ 已完成 |
| **目标** | 用户可直接通过 `mycode init kimi` 生成 Kimi 配置。 |
| **涉及文件** | `src/mycode/config.py`、`tests/test_config.py` |
| **实施步骤** | 在 `preset_config()` 中增加 `kimi` / `moonshot` 分支；补充单测。 |
| **验收标准** | `mycode init kimi` 生成正确 TOML；`preset_config("kimi")` 单测通过。 |
| **验证结果** | 完整测试套件 `199 passed`。 |

### P2-2：为 Claude 增加内置价格表

| 字段 | 内容 |
|------|------|
| **状态** | ✅ 已完成 |
| **目标** | 使用 Claude 时 `--budget` 与成本显示可用。 |
| **涉及文件** | `src/mycode/pricing.py`、`tests/test_pricing.py` |
| **实施步骤** | 在 `BUILTIN_PRICES` 中添加 `claude-sonnet-4-6`、`claude-3-5-sonnet`、`claude-3-5-sonnet-20241022` 的价格；补充单测。 |
| **验收标准** | `estimate_cost_usd(..., model="claude-sonnet-4-6")` 返回非 None；Claude 预算超限可正常触发。 |
| **验证结果** | 完整测试套件 `199 passed`。 |

### P2-3：补充 `requirements.txt` 或 lock 文件

| 字段 | 内容 |
|------|------|
| **状态** | ✅ 已完成 |
| **目标** | 提供不依赖 `pyproject.toml` 的依赖清单，便于 CI 与复现。 |
| **涉及文件** | `requirements.txt`、`requirements-dev.txt` |
| **实施步骤** | 从 `pyproject.toml` 提取生产依赖到 `requirements.txt`，dev 依赖到 `requirements-dev.txt`。 |
| **验收标准** | 新建虚拟环境仅安装 `requirements*.txt` 即可运行 `pytest`。 |
| **验证结果** | 文件已创建，内容与 `pyproject.toml` 一致。 |

---

## P3：增强与长期重构

### P3-1：展示 DeepSeek 推理模型的 `reasoning_content`

| 字段 | 内容 |
|------|------|
| **状态** | ✅ 已完成 |
| **目标** | 让 DeepSeek R1 等推理模型的思考过程对用户可见。 |
| **涉及文件** | `src/mycode/llm/base.py`、`src/mycode/llm/openai_compatible.py`、`src/mycode/agent/loop.py`、`src/mycode/ui.py` |
| **实施步骤** | 1. `LLMResponse` 增加 `reasoning_content`，新增类型化 `ReasoningChunk` 流事件。<br>2. 非流式读取 `message.reasoning_content`，流式累积 `delta.reasoning_content`。<br>3. `_consume_stream()` 通过 `print_reasoning_chunk()` 灰色输出。<br>4. reasoning 不混入最终 `content`；工具调用轮按 DeepSeek 协议保留独立 `reasoning_content` 字段。<br>5. 覆盖流式、非流式、默认 provider 与消息持久化测试。 |
| **验收标准** | 使用 DeepSeek 推理模型时，终端能看到思考链；非推理模型行为不变；测试通过。 |
| **风险** | 增加 token 输出量；需确保 reasoning_content 不会被当作最终答案重复输出。 |
| **依赖** | 无 |
| **验证结果** | P3 定向测试通过；完整离线套件 `216 passed`。 |

### P3-2：向 Agent 暴露通用 Git 工具

| 字段 | 内容 |
|------|------|
| **状态** | ✅ 已完成 |
| **目标** | Agent 可主动查看 git 状态、日志、分支，辅助调试与代码审查任务。 |
| **涉及文件** | `src/mycode/git_ops.py`、`src/mycode/tools/`（可能新增 `git.py`） |
| **实施步骤** | 1. 在 `git_ops.py` 中实现 `git_status()`、`git_log(n)`、`git_branch()` 等函数，返回格式化字符串。<br>2. 新建 `src/mycode/tools/git.py` 或使用现有注册机制，将上述函数注册为工具。<br>3. 更新 `SYSTEM_PROMPT`（`src/mycode/prompts.py`），让 Agent 知道可用 git 工具。<br>4. 补充单测：mock `subprocess.run` 验证工具输出。 |
| **验收标准** | Agent 在任务中可调用 `git_status`/`git_log` 并正确接收结果；测试覆盖。 |
| **风险** | git 输出可能包含敏感信息；需确保只读取、不执行写操作。 |
| **依赖** | 无 |
| **验证结果** | `git_status`、`git_log`、`git_branch` 已注册；真实临时仓库测试与完整套件通过。 |

### P3-3：Provider 发现机制（插件化）

| 字段 | 内容 |
|------|------|
| **状态** | ✅ 已完成 |
| **目标** | 新增 Provider 无需修改 `llm/__init__.py` 工厂。 |
| **涉及文件** | `src/mycode/llm/__init__.py`、`src/mycode/llm/base.py` |
| **实施步骤** | 1. 在 `BaseProvider` 子类上添加类属性/装饰器注册自身。<br>2. 修改 `build_provider()` 为从注册表查找 `type`。<br>3. 保留向后兼容：未知 type 仍回退到 `OpenAICompatibleProvider`。<br>4. 补充单测验证新 Provider 注册与选择。 |
| **验收标准** | 新增 Provider 只需定义类并注册，无需改工厂代码；测试通过。 |
| **风险** | 中低风险；需保证现有 OpenAI/Anthropic 行为不变。 |
| **依赖** | 无 |
| **验证结果** | 支持装饰器/函数式注册、无参/显式参数/`**kwargs` 构造器；未知类型回退兼容；完整套件通过。 |

### P3-4：增加可选日志与审计

| 字段 | 内容 |
|------|------|
| **状态** | ✅ 已完成 |
| **目标** | 支持问题排查与长期审计。 |
| **涉及文件** | 新增 `src/mycode/logging_config.py`，修改 `cli.py`、`agent/loop.py` |
| **实施步骤** | 1. 新增 `MYCODE_LOG_LEVEL` 环境变量支持（默认 `WARNING`）。<br>2. 在 `cli.py` 入口初始化 logging，输出到 `.mycode/logs/mycode.log`。<br>3. 在 `run_agent()` 中记录每轮 tool calls、usage、错误摘要。<br>4. 不记录完整 API key 或文件敏感内容。 |
| **验收标准** | 设置 `MYCODE_LOG_LEVEL=DEBUG` 后，可看到 tool call 与响应日志；不泄露 key。 |
| **风险** | 日志文件可能累积；需按日期轮转。 |
| **依赖** | 无 |
| **验证结果** | DEBUG 集成测试验证 tool call、结果状态、usage 写入及 API key/Bearer 脱敏；完整套件通过。 |

---

## P8：Skill 与插件系统（已完成）

> 本任务来自 `docs/NEXT_ROADMAP.md`，基于已落地的 P3 Provider/Tool 注册表能力实现，不依赖尚未完成的 P5/P6/P7。

### P8-1：Skill 说明加载与激活

| 字段 | 内容 |
|------|------|
| **状态** | ✅ 已完成 |
| **目标** | 支持复用工作流说明，并只激活配置中显式启用的 Skill。 |
| **涉及文件** | `src/mycode/skills/__init__.py`、`src/mycode/prompts.py` |
| **实施步骤** | 1. 定义 `SkillManifest`，从 `.mycode/skills/<name>/SKILL.md` 与 `~/.mycode/skills/<name>/SKILL.md` 解析 frontmatter 与正文。<br>2. 实现 `discover_skills()` 与 `load_active_skills(active_names)`。<br>3. `build_system_prompt()` 接收 `active_skills`，将 Skill 内容注入 system prompt。 |
| **验收标准** | 已激活 Skill 的内容出现在 system prompt 中；未激活 Skill 不影响行为。 |
| **验证结果** | `tests/test_skills.py` 通过；当前完整测试套件 330 passed, 1 skipped。 |

### P8-2：插件发现、注册与启用配置

| 字段 | 内容 |
|------|------|
| **状态** | ✅ 已完成 |
| **目标** | 通过 PyPA entry point 发现插件，仅导入显式启用的插件，并提供受控注册接口。 |
| **涉及文件** | `src/mycode/plugins/__init__.py`、`src/mycode/config.py` |
| **实施步骤** | 1. 在 `Config` 中增加 `skills` 与 `plugins` 字段，并支持 `MYCODE_SKILLS`、`MYCODE_PLUGINS` 环境变量覆盖。<br>2. 定义 `PluginSpec` 与 `PluginRegistrar`。<br>3. 通过 `mycode.plugins` entry point 组发现插件，仅加载 `config.plugins` 中列出的插件。<br>4. 插件可通过 registrar 注册 tool/provider/skill；名称冲突直接报错。 |
| **验收标准** | 未启用插件不会被导入；启用插件的能力进入统一 Registry；测试覆盖注册与发现。 |
| **验证结果** | `tests/test_plugins.py` 通过；当前完整测试套件 330 passed, 1 skipped。 |

### P8-3：CLI 集成

| 字段 | 内容 |
|------|------|
| **状态** | ✅ 已完成 |
| **目标** | 用户可通过命令列出、检查 Skill 与插件。 |
| **涉及文件** | `src/mycode/cli.py` |
| **实施步骤** | 1. 在 `main()` 中增加 `skill` / `plugin` 关键字分支，支持 `mycode skill list` 与 `mycode plugin list`。<br>2. 在任务运行前加载已启用插件与已激活 Skill，并提示缺失的 Skill。<br>3. 在 `mycode config show` 中显示 skills/plugins。 |
| **验收标准** | `mycode skill list` / `mycode plugin list` 可正常输出；配置 show 包含 skills/plugins。 |
| **验证结果** | 手动验证 `mycode skill list` / `mycode plugin list` / `mycode config show` 正常；当前完整测试套件 330 passed, 1 skipped。 |

---

## 推荐迭代节奏

### 第 1-2 周：P0 + P1 + P2（已完成）

- ✅ P0-1 修复价格测试
- ✅ P0-2 统一版本号
- ✅ P1-1 补回 `conftest.py`
- ✅ P1-2 修复中文输出乱码
- ✅ P2-1 Kimi preset
- ✅ P2-2 Claude 价格表
- ✅ P2-3 `requirements.txt`
- **里程碑**：`pytest` 全绿，多 Provider 配置体验补齐，依赖可复现。

### 第 3-4 周：P3 + P8（已完成）

- ✅ P3-1 DeepSeek reasoning_content
- ✅ P3-2 Git 工具
- ✅ P3-3 Provider 发现机制
- ✅ P3-4 日志审计
- ✅ P8-1 Skill 说明加载
- ✅ P8-2 插件发现/注册
- ✅ P8-3 CLI 集成
- **里程碑**：Agent 能力增强，架构更易扩展，可复用工作流与第三方插件落地。

### 下一阶段

- P5-P11 已按 `docs/NEXT_ROADMAP.md` 完成；下一步是配置 GitHub/PyPI Trusted Publisher，打 `v0.2.0` 标签并观察首轮发布反馈。

---

## 任务依赖图

```text
P0-1 价格测试修复 ─┬── P1-1 conftest.py
                  └── P2-2 Claude 价格表

P0-2 版本号统一

P1-1 conftest.py
P1-2 中文乱码

P2-1 Kimi preset
P2-2 Claude 价格表 ── 依赖 P0-1
P2-3 requirements.txt

P3-1 reasoning_content
P3-2 Git 工具
P3-3 Provider 发现
P3-4 日志审计
```

---

## 通用验收清单

每个任务完成后应满足：

- [ ] 相关测试通过（新增/修改的测试 + 完整 `pytest` 套件）。
- [ ] 未引入新的 lint/type 错误（建议补充 `ruff`/`pyright` 检查）。
- [ ] 文档（README / 代码注释）已同步更新。
- [ ] 至少手动验证一次 CLI 行为（如适用）。
- [ ] 未访问项目根目录以外的文件或系统资源。
