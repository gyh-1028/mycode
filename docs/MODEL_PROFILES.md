# 模型配置档与长期凭据

MyCode 支持保存多个模型配置并随时切换。模型名称、Provider 类型和 API 地址保存在 `~/.mycode/models.toml`；API Key 不写入 TOML，而是通过 Python `keyring` 保存到 Windows Credential Manager、macOS Keychain 或 Linux Secret Service。

## 快速使用

```cmd
mycode model presets
mycode model add deepseek --preset deepseek
mycode model add claude --preset anthropic
mycode model list
mycode model use claude
mycode doctor
```

`model add` 会隐藏输入 API Key，并要求输入两次确认。不要把 Token 直接写在命令参数中，否则会进入 shell 历史。

更新已有 Token：

```cmd
mycode model key deepseek
```

修改模型或端点：

```cmd
mycode model edit deepseek --model deepseek-chat
mycode model edit local --base-url http://localhost:8000/v1
```

删除配置和对应系统凭据：

```cmd
mycode model remove deepseek --force
```

加 `--keep-key` 只删除模型配置，保留系统凭据。

## 内置预设

- `openai`
- `deepseek`
- `anthropic`
- `kimi`（Kimi 开放平台 API）
- `kimi-coding`（Kimi Coding Plan）
- `qwen`
- `gemini`
- `glm`
- `minimax`

Web 工作台提供更完整的模型目录和推理参数：

| 渠道 | 主要模型 | 推理控制 |
| --- | --- | --- |
| OpenAI | GPT-5.5、GPT-5.4、GPT-5.4 mini/nano | `reasoning_effort` |
| Kimi Coding Plan | `kimi-for-coding` | Thinking 固定开启；`low`/`high` |
| Kimi 开放平台 | Kimi K2.7 Code、K2.6、K2.5、Moonshot V1 | K2.7 Code 固定 Thinking；K2.6/K2.5 可切换 |
| 阿里云百炼 | Qwen3.7、Qwen3.6、Qwen3 Coder | 思考开关与 Token 预算 |
| Anthropic | Claude Opus 4.8、Sonnet 5、Sonnet 4.6 | adaptive thinking 与 effort |
| Google | Gemini 3.5 Flash、3.1 Pro Preview、3 Flash Preview | `reasoning_effort` |
| MiniMax | M2.7、M2.7 Highspeed、M2.5 系列 | 固定推理 |

Kimi 的两个渠道不是同一个服务：Coding Plan 使用会员额度、
`https://api.kimi.com/coding/v1` 和固定模型名 `kimi-for-coding`；开放平台按量计费，
使用 `https://api.moonshot.cn/v1` 和 `kimi-k2.7-code`、`kimi-k2.6` 等模型。两边的 API Key
不能混用。Coding Plan 当前只提供 `low`/`high` 两档推理程度，不显示无效的 medium/xhigh。

自定义 OpenAI 兼容服务：

```cmd
mycode model add local --provider openai --model my-model --base-url http://localhost:8000/v1 --no-key
```

## 凭据优先级

运行时依次检查：

1. 配置档指定的环境变量。
2. 操作系统凭据库。

因此 CI 可以继续使用环境变量，本地开发可以使用长期系统凭据。`mycode config show` 和 `mycode doctor` 只显示凭据是否存在及来源，不显示 Token 内容。

如果系统没有可用 keyring backend，MyCode 会拒绝明文保存，并提示修复系统凭据服务；不会退化为明文配置文件。
