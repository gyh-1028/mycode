# 真实模型 Eval

真实模型 Eval 与普通 `pytest`、FakeProvider Eval 分离，不会在 CI 中自动调用 API。

## 套件

- `safe-core-v1`: 20 个任务。只允许文件读取、搜索、编辑和代码智能工具，不允许 Shell、MCP、插件或项目 Skill。
- `host-repair-v1`: 6 个 pytest 修复任务。会执行工作区代码，必须显式允许。
- `codeintel-v1`: 12 个跨文件 Python/TypeScript 任务，用于比较精准上下文开关。

## 运行

```powershell
mycode eval live --suite safe-core-v1 --repeat 1 --budget 1
mycode eval live --suite safe-core-v1 --repeat 3 --budget 1

# 高风险：在本机临时工作区执行测试命令
mycode eval live --suite host-repair-v1 --unsafe-allow-host-exec --budget 1
```

结果保存在 `.mycode/evals/runs/<run-id>/`。其中包括 `run.json`、`summary.json`、逐任务工作区和只记录元数据的 JSONL Trace，不包含 API Key。

未知模型价格默认拒绝执行。可在配置的 `[pricing.<model>]` 中提供价格；`--allow-unknown-pricing` 只适合明确接受无法执行成本硬限制的场景。

## 对比

```powershell
mycode eval report <run-id>
mycode eval compare <baseline-run-id> <candidate-run-id>
mycode eval baseline update <run-id> --force

mycode eval live --suite codeintel-v1 --repeat 3 --auto-context off
mycode eval live --suite codeintel-v1 --repeat 3 --auto-context on
```

代码智能验收门槛为同模型成功率提高至少 10 个百分点，同时提示词 token 增幅不超过 20%。正式基线使用三次重复，快速开发检查使用一次重复。
