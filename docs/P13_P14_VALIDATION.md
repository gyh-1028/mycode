# P13-P14 验证记录

验证日期：2026-07-02，环境：Windows、Python 3.14。

## 已通过

- Ruff：全部通过。
- 离线 pytest：359 passed，1 skipped。
- P13/P14 定向回归：28 passed。
- 真实 Eval 任务清单：safe 20、host 6、codeintel 12。
- 仓库索引烟雾测试：278 files、1240 symbols、692 dependencies，零索引错误。
- 当前模型 `deepseek-chat` 具有内置价格，可执行 1 USD 预算控制。

## 待外部条件

当前进程未设置 `DEEPSEEK_API_KEY`，因此没有调用真实 API，也没有生成三次重复的任务成功率基线。配置 Key 后按以下命令执行：

```powershell
mycode eval live --suite safe-core-v1 --repeat 3 --budget 1
mycode eval live --suite codeintel-v1 --repeat 3 --auto-context off --budget 1
mycode eval live --suite codeintel-v1 --repeat 3 --auto-context on --budget 1
```

## 未达到的性能目标

10,000 个小型 Python 文件的本机基准：

- 冷索引：53.539 秒，目标小于 30 秒。
- 修改 100 个文件后的增量索引：最佳 2.647 秒，目标小于 2 秒。

基准期间 CPU 使用率低，主要等待 Windows 文件 I/O 和实时扫描。索引器已采用内容哈希、mtime/size 快速路径、并行读取与单事务写入，但当前机器仍未达到目标。后续应增加 Git 变更集快速路径，并评估批量解析或独立后台索引进程。
