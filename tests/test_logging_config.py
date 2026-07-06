"""Logging and audit tests."""

import logging

from mycode.agent.loop import run_agent
from mycode.llm.base import LLMResponse, StopReason, ToolCall, Usage
from mycode.logging_config import setup_logging
from tests.fakes import FakeProvider, quiet_console


def test_debug_logging_audits_tools_and_redacts_secrets(tmp_path, monkeypatch) -> None:
    secret = "sk-test-secret-1234"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
    logger = logging.getLogger("mycode")
    before = set(logger.handlers)
    setup_logging(log_level="DEBUG", log_dir=tmp_path)
    added = [handler for handler in logger.handlers if handler not in before]

    try:
        logger.warning("authorization: Bearer %s api_key=%s", secret, secret)
        provider = FakeProvider(
            [
                LLMResponse(
                    tool_calls=[
                        ToolCall(
                            id="c1",
                            name="list_files",
                            args={"path": str(tmp_path)},
                        )
                    ],
                    stop_reason=StopReason.TOOL_CALLS,
                    usage=Usage(prompt_tokens=10, completion_tokens=2),
                ),
                LLMResponse(text="done", stop_reason=StopReason.END_TURN),
            ]
        )

        run_agent(
            provider,
            [{"role": "user", "content": "inspect"}],
            tools=[],
            console=quiet_console(),
        )
        for handler in added:
            handler.flush()

        content = (tmp_path / "mycode.log").read_text(encoding="utf-8")
        assert "tool call: list_files" in content
        assert "tool result: list_files status=ok" in content
        assert "step usage prompt=10 completion=2" in content
        assert secret not in content
        assert "[REDACTED]" in content
    finally:
        for handler in added:
            logger.removeHandler(handler)
            handler.close()
