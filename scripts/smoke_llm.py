"""Manual smoke test: real LLM round-trip using mycode's config + provider.

Run it directly:

    python scripts/smoke_llm.py
    python scripts/smoke_llm.py "用一句话解释 Flask 是什么"

It loads `.mycode/config.toml` (or defaults), resolves the API key from the
configured environment variable, calls the model once, and prints the
normalized LLMResponse. Exits non-zero with a friendly message if the key is
missing or the request fails.
"""

import sys

from mycode.config import ConfigError, load_config
from mycode.llm.openai_compatible import OpenAICompatibleProvider


def main(argv: list[str]) -> int:
    prompt = argv[1] if len(argv) > 1 else "用一句话解释 Flask 是什么"

    config = load_config()
    try:
        api_key = config.provider.resolve_api_key()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    provider = OpenAICompatibleProvider(
        api_key=api_key,
        model=config.default_model,
        base_url=config.provider.base_url,
    )

    print(f"模型:{config.default_model}  base_url:{config.provider.base_url}")
    print(f"提问:{prompt}\n")

    try:
        resp = provider.chat([{"role": "user", "content": prompt}])
    except Exception as exc:  # noqa: BLE001 - 烟雾脚本,直接把错误报给用户
        print(f"调用失败:{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print("=== text ===")
    print(resp.text)
    print(f"\nstop_reason: {resp.stop_reason}")
    print(f"tool_calls : {len(resp.tool_calls)}")
    print(
        "usage      : "
        f"prompt={resp.usage.prompt_tokens} "
        f"completion={resp.usage.completion_tokens} "
        f"total={resp.usage.total_tokens}"
    )
    return 0 if (resp.text and resp.text.strip()) else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
