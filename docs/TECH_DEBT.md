# MyCode 技术债与问题清单

> 生成时间：2026/06/30  
> P0、P1、P2 与恢复计划中的 4 项 P3 已修复；其余低优先级观察项保留。

---

## 1. 高优先级（P0/P1）

### 1.1 版本号不一致

| 项目 | 内容 |
|------|------|
| 位置 | `pyproject.toml:7` vs `src/mycode/__init__.py:3` |
| 状态 | **已修复** |
| 修复内容 | 将 `pyproject.toml` 的 `version` 从 `"0.0.1"` 改为 `"0.1.1"`，并重新执行 `pip install -e . --no-deps`。 |
| 验证 | `python -m mycode --version` 与 `pip show mycode` 均显示 `0.1.1`。 |

### 1.2 价格估算单测失败

| 项目 | 内容 |
|------|------|
| 位置 | `tests/test_pricing.py:35` |
| 状态 | **已修复** |
| 问题 | `test_estimate_cost_uses_config_override` 期望 `3.275`，实际 `2.875`。 |
| 根因 | 测试预期值过期。当前 `pricing.py` 的计费逻辑将 `cached_tokens` 和 `cache_write_tokens` 从 `prompt_tokens` 中扣除后按 cache 单价计费，语义正确。测试期望 `3.275` 对应的计费逻辑会导致 cache token 被双重计费。 |
| 修复内容 | 将测试断言从 `assert cost == 3.275` 改为 `assert cost == 2.875`。 |
| 验证 | `pytest tests/test_pricing.py` 全绿；完整测试套件 `196 passed`。 |

---

## 2. 中优先级（P2）

### 2.1 终端中文输出乱码

| 项目 | 内容 |
|------|------|
| 位置 | CLI 所有中文输出 |
| 状态 | **已修复** |
| 问题 | 在 Windows PowerShell 5 / 旧版终端下，中文显示为乱码或问号。 |
| 修复内容 | 在 `src/mycode/cli.py` 入口增加 `_ensure_utf8_stdio()`：<br>1. 通过 `ctypes.windll.kernel32.SetConsoleOutputCP/SetConsoleCP` 将控制台代码页设为 CP_UTF8（仅在真实控制台时）。<br>2. 将 `sys.stdout`/`sys.stderr` 重配为 `utf-8`（Windows 下当前编码非 UTF-8 时）。 |
| 验证 | `python -m mycode --help` 与 `python -m mycode doctor` 中文正常显示；重定向到文件仍生成 UTF-8 内容；完整测试套件 `196 passed`。 |

### 2.2 `conftest.py` 缺失与临时目录配置不一致

| 项目 | 内容 |
|------|------|
| 位置 | `tests/conftest.py`（新建） |
| 状态 | **已修复** |
| 问题 | `pyproject.toml` 注释说明 `PYTEST_DEBUG_TEMPROOT` 由 `conftest.py` 设置，但项目根目录没有 `conftest.py`。 |
| 修复内容 | 新建 `tests/conftest.py`，在 `pytest_configure` 中设置 `PYTEST_DEBUG_TEMPROOT` 为项目本地 `.pytmp`。 |
| 验证 | 测试临时目录生成在 `.pytmp/run-<pid>` 下；完整测试套件 `196 passed`；连续三次全量跑测稳定通过。 |

### 2.3 缺少 Kimi (Moonshot) Provider Preset

| 项目 | 内容 |
|------|------|
| 位置 | `src/mycode/config.py:preset_config()` |
| 状态 | **已修复** |
| 问题 | `preset_config()` 只支持 `deepseek`、`openai`、`anthropic`，没有 `kimi` / `moonshot`。 |
| 修复内容 | 在 `preset_config()` 中增加 `kimi` / `moonshot` 分支，默认模型 `moonshot-v1-8k`，`api_key_env = "MOONSHOT_API_KEY"`，`base_url = "https://api.moonshot.cn"`。 |
| 验证 | `preset_config("kimi")` 与 `preset_config("moonshot")` 单测通过；完整测试套件 `199 passed`。 |

### 2.4 Claude 无内置价格表

| 项目 | 内容 |
|------|------|
| 位置 | `src/mycode/pricing.py:26-29` |
| 状态 | **已修复** |
| 问题 | `BUILTIN_PRICES` 只有 `gpt-4o-mini` 和 `deepseek-chat`。 |
| 修复内容 | 为 `claude-sonnet-4-6`、`claude-3-5-sonnet`、`claude-3-5-sonnet-20241022` 增加内置价格（input/output/cache_read/cache_write）。 |
| 验证 | `estimate_cost_usd(..., model="claude-sonnet-4-6")` 返回非 None；新增 Claude 成本单测通过；完整测试套件 `199 passed`。 |

### 2.5 缺少 `requirements.txt`

| 项目 | 内容 |
|------|------|
| 位置 | 项目根目录 |
| 状态 | **已修复** |
| 问题 | 只有 `pyproject.toml` 中的依赖声明，没有独立的 `requirements.txt` 或 lock 文件。 |
| 修复内容 | 新增 `requirements.txt`（生产依赖）和 `requirements-dev.txt`（开发依赖），内容与 `pyproject.toml` 一致。 |
| 验证 | 可通过 `pip install -r requirements.txt -r requirements-dev.txt` 安装依赖并运行测试。 |

---

## 3. 低优先级（P3）

### 3.1 DeepSeek `reasoning_content` 支持

| 项目 | 内容 |
|------|------|
| 状态 | **已修复** |
| 位置 | `src/mycode/llm/openai_compatible.py` |
| 问题 | DeepSeek R1 等推理模型会在响应中返回 `reasoning_content`，当前代码只读取 `content`，未展示推理过程。 |
| 影响 | 使用推理模型时，用户看不到模型思考链；可能导致模型“只给结论”的体验。 |
| 建议 | 在 `LLMResponse` 中增加可选的 `reasoning_content` 字段，流式输出时一并打印（可折叠或带前缀）。 |
| 修复内容 | 增加 `reasoning_content` 与 `ReasoningChunk`；支持流式/非流式解析和灰色独立展示；工具调用轮保留独立 reasoning 字段，不混入最终答案。 |

### 3.2 Git 只读工具

| 项目 | 内容 |
|------|------|
| 状态 | **已修复** |
| 位置 | `src/mycode/git_ops.py` |
| 问题 | 仅支持 `--commit` 自动提交和 `/diff` 渲染，未向 Agent 暴露 `git_status`、`git_log`、`git_branch` 等工具。 |
| 影响 | Agent 无法主动查看 git 状态，某些任务（如“基于最近提交修复回归”）受限。 |
| 建议 | 新增 `git_status`、`git_log` 等工具注册到 Tool Registry。 |
| 修复内容 | 新增并注册只读 `git_status`、`git_log`、`git_branch`，更新系统提示并补真实仓库测试。 |

### 3.3 Provider 注册机制

| 项目 | 内容 |
|------|------|
| 状态 | **已修复** |
| 位置 | `src/mycode/llm/__init__.py:18` |
| 问题 | Provider 工厂是硬编码的 `if type == "anthropic"` 分支，新增 Provider 需修改工厂。 |
| 影响 | 插件化扩展困难。 |
| 建议 | 可考虑基于 entry points 或注册表的 Provider 发现机制；当前阶段优先级低。 |
| 修复内容 | 增加 Provider 注册表与 `@register_provider`；工厂支持无参、显式参数和 `**kwargs` 构造器，未知类型保持兼容回退。 |

### 3.4 配置校验与模型白名单

| 项目 | 内容 |
|------|------|
| 位置 | `src/mycode/config.py` |
| 问题 | `default_model` 是任意字符串，无模型白名单或格式校验。 |
| 影响 | 用户可能写错模型名，直到运行时 API 调用失败才发现。 |
| 建议 | 低优先级；可在 `doctor` 中增加常见模型名提示，而非强制校验。 |

### 3.5 测试对 `Path.cwd()` 的隐式依赖

| 项目 | 内容 |
|------|------|
| 位置 | `tests/test_cli.py`、`tests/test_session.py` 等 |
| 问题 | 部分测试依赖 `tmp_path` 和 `monkeypatch.chdir`，若测试并发或目录切换处理不当可能互相影响。 |
| 影响 | 当前测试通过，但并发执行（如 `pytest -n auto`）可能存在风险。 |
| 建议 | 评估是否需要 `pytest-xdist` 兼容；当前单线程无问题。 |

### 3.6 全局日志/审计

| 项目 | 内容 |
|------|------|
| 状态 | **已修复** |
| 位置 | 全局 |
| 问题 | 没有统一的日志模块，错误信息直接打印到控制台；无审计日志记录 tool calls。 |
| 影响 | 排查问题时依赖复现；无法长期审计 Agent 行为。 |
| 建议 | 增加可选的 `MYCODE_LOG_LEVEL` 与文件日志。 |
| 修复内容 | 增加按日轮转日志，CLI 初始化，记录 tool/usage/error 摘要；统一脱敏 Bearer、API key 和敏感环境变量值。 |

---

## 4. 已知但未确认的问题

### 4.1 `stream_usage` 默认值与降级行为

- `OpenAICompatibleProvider.stream()` 默认传 `stream_options={"include_usage": True}`，部分第三方兼容端点可能不支持但会 200 返回错误体；当前降级逻辑捕获异常后重试。需要确认降级不会导致首 token 延迟显著增加。

### 4.2 上下文压缩阈值

- 当前阈值 `0.7 * context_limit`，摘要本身也会消耗 token；在上下文接近极限时，可能一次压缩后很快再次触发。可观察实际使用中的压缩频率。

### 4.3 大文件与二进制文件检测

- 文件工具通过换行符比例和 NUL 字节检测二进制；某些编码或特殊文件可能误判。当前测试覆盖常见情况。

---

## 5. 债务汇总表

| # | 问题 | 优先级 | 涉及文件 | 修复成本 | 风险 |
|---|------|--------|----------|----------|------|
| 1 | ~~版本号不一致~~ | ~~P1~~ | `pyproject.toml`, `src/mycode/__init__.py` | 低 | 已修复 |
| 2 | ~~价格测试失败~~ | ~~P1~~ | `tests/test_pricing.py` | 低 | 已修复 |
| 3 | ~~终端中文乱码~~ | ~~P2~~ | `src/mycode/cli.py` | 低 | 已修复 |
| 4 | ~~`conftest.py` 缺失~~ | ~~P2~~ | `tests/conftest.py`, `pyproject.toml` | 低 | 已修复 |
| 5 | ~~无 Kimi preset~~ | ~~P2~~ | `src/mycode/config.py` | 低 | 已修复 |
| 6 | ~~Claude 无价格~~ | ~~P2~~ | `src/mycode/pricing.py` | 低 | 已修复 |
| 7 | ~~缺少 `requirements.txt`~~ | ~~P2~~ | `requirements*.txt` | 低 | 已修复 |
| 8 | ~~DeepSeek reasoning_content 未处理~~ | ~~P3~~ | `src/mycode/llm/openai_compatible.py`, `src/mycode/llm/base.py` | 中 | 已修复 |
| 9 | ~~Git 工具不完整~~ | ~~P3~~ | `src/mycode/git_ops.py`, `src/mycode/tools/` | 中 | 已修复 |
| 10 | ~~Provider 工厂硬编码~~ | ~~P3~~ | `src/mycode/llm/__init__.py` | 中 | 已修复 |
| 11 | ~~缺少日志/审计~~ | ~~P3~~ | 全局 | 中 | 已修复 |

---

## 6. 新增强能力（非债务，来自 P8）

| # | 能力 | 涉及文件 | 状态 |
|---|------|----------|------|
| 1 | Skill 工作流说明 | `src/mycode/skills/__init__.py`, `src/mycode/prompts.py` | 已落地 |
| 2 | 插件发现与受控注册 | `src/mycode/plugins/__init__.py`, `src/mycode/config.py` | 已落地 |
| 3 | Skill/Plugin CLI 列表 | `src/mycode/cli.py` | 已落地 |

Skill 与插件均默认禁用，必须显式激活/启用；未启用插件不会被导入，降低了意外代码执行风险。

---

## 7. 建议的修复/演进顺序

1. **已修复**：版本号、价格测试失败、终端中文乱码、`conftest.py`、Kimi preset、Claude 价格表、`requirements.txt`。
2. **已修复**：DeepSeek reasoning_content、git 工具、Provider 注册机制、日志审计。
3. **已落地（P8）**：Skill 工作流、插件发现/注册、CLI 集成。
4. **后续观察（按需）**：配置模型提示、pytest-xdist 兼容、上下文压缩阈值与二进制检测。
5. **已落地（P5-P11）**：Trace、Evals、MCP、TUI、stdio 协议、VS Code 和发布链路均已完成；下一阶段聚焦真实用户反馈、跨平台 CI 结果和 0.2.x 稳定性修复。
