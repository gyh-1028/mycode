# 代码智能与精准上下文

MyCode 在本机维护 SQLite 符号索引，并可连接现有 Python/TypeScript Language Server。索引不会上传到独立服务；只有最终加入模型请求的片段会发送给当前配置的模型。

## 索引命令

```powershell
mycode index build
mycode index status
mycode index clear --force
mycode doctor
```

索引位于 `.mycode/index/codeintel-v1.sqlite3`。Git 仓库优先使用 `git ls-files`；非 Git 目录使用安全遍历。敏感文件、依赖目录和构建目录不会进入索引。

Python 在没有 LSP 时使用标准库 AST 提取类、函数、方法和 import。TypeScript 没有 LSP 时降级为路径和词法搜索。

## Language Server

MyCode 不自动下载语言服务器。Python 查找项目 `.venv` 和 PATH 中的 `pyright-langserver`；TypeScript 查找项目 `node_modules/.bin` 和 PATH 中的 `typescript-language-server`。启动遵循 `permissions.command`，失败时 Agent 继续使用本地降级能力。

```toml
[codeintel]
enabled = true
auto_context = true
max_context_tokens = 12000
max_context_fraction = 0.20
max_files = 12
max_chunks = 30
lsp_timeout = 5.0

[codeintel.language_servers]
python = ["pyright-langserver", "--stdio"]
typescript = ["typescript-language-server", "--stdio"]
```

自动上下文在每次模型调用前临时生成，不写入 Session。`context.selected` 事件记录路径、符号、分数、原因和 token 估算；正文只有在 Trace 启用 prompt 记录时才会保存。

`codeintel.enabled = false` 关闭全部代码智能；`auto_context = false` 只关闭自动选择，符号和 LSP 工具仍可供 Agent 调用。
