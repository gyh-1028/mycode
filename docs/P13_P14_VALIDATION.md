# P13-P14 验证记录

> 更新日期：2026-07-14
>
> 环境：Windows，Python 3.14.4。

## 离线质量门禁

| 检查 | 结果 |
| --- | --- |
| Ruff | 通过 |
| Pyright | `0 errors` |
| Python 全量测试 | `425 passed, 1 skipped` |
| 覆盖率 | `80.38%` |
| FakeProvider 离线 Eval | `6/6` 通过，`0` regression |
| Web Vitest | `12/12` 通过 |
| Web Playwright | 深浅主题、审批、模型、文件和 3 种尺寸通过 |

唯一跳过项依赖真实外部条件。普通测试不访问网络，也不要求 API Key。

## P13 真实模型 Eval

已实现：

- `safe-core-v1`：20 个安全文件任务。
- `host-repair-v1`：6 个需要显式允许本机执行的修复任务。
- `codeintel-v1`：12 个跨文件代码智能任务。
- 重复运行、预算、价格未知拒绝、JSON 报告、baseline 和 compare。
- 临时工作区、Shell/MCP/插件隔离和越界写入评分。

尚未执行正式三次重复基线。该步骤需要有效 API Key 并会产生费用，不能由普通测试或无明确
费用授权的自动流程代替。

```powershell
mycode doctor
mycode eval live --suite safe-core-v1 --repeat 3 --budget 1
mycode eval live --suite codeintel-v1 --repeat 3 --auto-context off --budget 1
mycode eval live --suite codeintel-v1 --repeat 3 --auto-context on --budget 1
```

运行前必须确认 `doctor` 显示当前模型价格已知或已在配置中提供价格。目录验证日期为空时，
还应先对照 Provider 官方文档确认模型 ID 和能力。

## P14A 符号索引与 LSP

已验证：

- SQLite 增量索引、Python AST、依赖边、Windows 路径和权限拒绝。
- LSP framing、定义、引用、诊断、超时、崩溃降级和进程复用。
- SQLite 连接在事务结束后关闭。
- 显式变更路径与 Git change-set 可跳过全仓库重新扫描。
- 大 Git 仓库可通过 `git cat-file --batch` 读取 clean tracked blob。

性能结果：

| 场景 | 本机结果 | 目标 | 状态 |
| --- | --- | --- | --- |
| 非 Git 10,000 小文件冷索引 | 约 `72-98s` | `<30s` | 未达到 |
| 100 个变更文件增量 | 约 `0.72s` | `<2s` | 已达到 |

大 Git 仓库的 blob 快速路径已经实现并有行为测试，但尚未完成 10,000 文件端到端性能复测。

```powershell
python scripts/benchmark_codeintel.py --files 10000 --changes 100
python scripts/benchmark_codeintel.py --files 10000 --changes 100 --git
```

## P14B 精准上下文

已验证 ContextSelector 的显式文件优先级、符号匹配、依赖邻接、去重、token/file/chunk 限额、
索引失效和 `context.selected` 事件。LSP 不可用时 Runner 会继续使用 AST 与词法降级。

效果验收仍待真实模型 A/B：

- 成功率提升至少 10 个百分点。
- 平均提示词 token 增幅不超过 20%。
- 不新增越界读取、敏感文件读取或意外写入。

## 结论

P13/P14 的实现与离线正确性门禁已经完成。真实模型效果门禁和冷索引性能门禁尚未全部完成，
因此不能宣称 P13/P14 的最终效果验收已通过。
