"""Live smoke test against a real OpenAI-compatible endpoint (DeepSeek).

Skipped automatically unless DEEPSEEK_API_KEY is set, so CI without a key stays
green. This is the Task 2 acceptance check: a real call must return non-empty
LLMResponse.text. Override model/base_url via MYCODE_LIVE_MODEL /
MYCODE_LIVE_BASE_URL if needed.
"""

import os

import pytest

from mycode.llm.base import StopReason
from mycode.llm.openai_compatible import OpenAICompatibleProvider

_API_KEY = os.environ.get("DEEPSEEK_API_KEY")


@pytest.mark.skipif(not _API_KEY, reason="需要设置 DEEPSEEK_API_KEY 才能跑真实调用")
def test_live_returns_nonempty_text() -> None:
    provider = OpenAICompatibleProvider(
        api_key=_API_KEY,
        model=os.environ.get("MYCODE_LIVE_MODEL", "deepseek-chat"),
        base_url=os.environ.get("MYCODE_LIVE_BASE_URL", "https://api.deepseek.com"),
    )
    resp = provider.chat([{"role": "user", "content": "用一句话解释 Flask 是什么"}])

    assert resp.text is not None and resp.text.strip(), "期望拿到非空的文本回复"
    assert resp.stop_reason in {
        StopReason.END_TURN,
        StopReason.MAX_TOKENS,
        StopReason.OTHER,
    }
