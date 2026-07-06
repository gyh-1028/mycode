"""Shared application runtime used by the TUI and machine protocol server."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mycode.agent.events import EventSink
from mycode.agent.runner import AgentRunner, AgentRunResult, CancellationToken, RunRequest
from mycode.approvals import (
    PERMISSION_MODES,
    ApprovalHandler,
    approval_handler,
    permission_scope,
)
from mycode.checkpoint import Checkpoint, reset_current_checkpoint, set_current_checkpoint
from mycode.codeintel.context import ContextSelector
from mycode.codeintel.tools import close_service
from mycode.config import ConfigLoadResult, load_config_result
from mycode.context import maybe_compact
from mycode.llm import BaseProvider, build_provider
from mycode.mcp import build_registry as build_mcp_registry
from mycode.mentions import expand_mentions
from mycode.plugins import load_enabled_plugins
from mycode.prompts import build_system_prompt
from mycode.session import Session
from mycode.skills import load_active_skills
from mycode.trace import TraceWriter
from mycode.trace_otel import OtelEventSink, init_otel_provider, shutdown_otel_provider

COLLABORATION_MODES = {"default", "plan", "review"}
READ_ONLY_TOOLS = {
    "find_definition",
    "find_files",
    "find_references",
    "get_diagnostics",
    "git_branch",
    "git_diff",
    "git_log",
    "git_status",
    "list_files",
    "read_file",
    "search_code",
    "search_symbols",
}
REVIEW_INSTRUCTION = (
    "你处于代码审查模式。只检查当前工作区和用户指定范围，不修改文件，不运行 shell 命令。"
    "先按严重程度列出具体问题，并给出文件与行号；重点关注缺陷、回归、安全风险和缺失测试。"
    "如果没有发现问题，要明确说明，并指出剩余测试风险。"
)


@dataclass(frozen=True)
class RuntimeInfo:
    project_root: Path
    model: str
    provider: str
    config_sources: tuple[str, ...]
    missing_skills: tuple[str, ...] = ()
    plugin_errors: tuple[str, ...] = ()


class MyCodeRuntime:
    """Owns provider/configuration and executes one prompt against a session."""

    def __init__(
        self,
        *,
        config_result: ConfigLoadResult,
        provider: BaseProvider,
        project_root: Path | None = None,
    ) -> None:
        self.config_result = config_result
        self.config = config_result.config
        self.provider = provider
        self.project_root = (project_root or Path.cwd()).resolve()
        if self.project_root != Path.cwd().resolve():
            raise ValueError("MyCodeRuntime 必须在项目根目录进程中创建")

        self.context_selector = (
            ContextSelector(
                self.project_root,
                self.config.codeintel,
                context_limit=self.config.context_limit,
            )
            if self.config.codeintel.enabled and self.config.codeintel.auto_context
            else None
        )

        plugin_errors: list[str] = []
        if self.config.plugins:
            try:
                _, plugin_errors = load_enabled_plugins(self.config.plugins)
            except RuntimeError as exc:
                plugin_errors = [str(exc)]
        self.active_skills, self.missing_skills = load_active_skills(self.config.skills)
        self.info = RuntimeInfo(
            project_root=self.project_root,
            model=self.config.default_model,
            provider=self.config.provider.type or "openai",
            config_sources=tuple(str(path) for path in config_result.files),
            missing_skills=tuple(self.missing_skills),
            plugin_errors=tuple(plugin_errors),
        )

    @classmethod
    def from_environment(cls, project_root: Path | None = None) -> MyCodeRuntime:
        result = load_config_result()
        api_key = result.config.provider.resolve_api_key()
        return cls(
            config_result=result,
            provider=build_provider(result.config, api_key),
            project_root=project_root,
        )

    @classmethod
    def from_config_result(
        cls,
        config_result: ConfigLoadResult,
        project_root: Path | None = None,
    ) -> MyCodeRuntime:
        api_key = config_result.config.provider.resolve_api_key()
        return cls(
            config_result=config_result,
            provider=build_provider(config_result.config, api_key),
            project_root=project_root,
        )

    def new_session(self, *, persist: bool = True) -> Session:
        messages = [
            {
                "role": "system",
                "content": build_system_prompt(
                    active_skills=self.active_skills,
                    provider=self.config.provider.type,
                    model=self.config.default_model,
                ),
            }
        ]
        session = Session.new(
            model=self.config.default_model,
            provider=self.config.provider.type or "openai",
            messages=messages,
        )
        if persist:
            session.save(messages)
        return session

    def get_session(self, session_id: str | None = None, *, persist: bool = True) -> Session:
        if session_id:
            session = Session.load(session_id)
            if session is None:
                raise LookupError(f"找不到会话:{session_id}")
            return session
        return self.new_session(persist=persist)

    def compact_session(self, session: Session) -> bool:
        """手动压缩会话上下文;仅在真正发生压缩时持久化。"""
        compacted = maybe_compact(
            self.provider,
            session.messages,
            context_limit=self.config.context_limit,
        )
        if compacted:
            session.save(session.messages)
        return compacted

    def list_sessions(self) -> list[Session]:
        return Session.list_all()

    def run_prompt(
        self,
        session: Session,
        prompt: str,
        *,
        sink: EventSink | None = None,
        cancellation_token: CancellationToken | None = None,
        approval: ApprovalHandler | None = None,
        budget_usd: float | None = None,
        run_id: str | None = None,
        collaboration_mode: str = "default",
        permission_mode: str = "standard",
    ) -> AgentRunResult:
        """Append a user turn, run the agent, and persist each consistent state."""

        if collaboration_mode not in COLLABORATION_MODES:
            raise ValueError(f"未知工作模式: {collaboration_mode}")
        if permission_mode not in PERMISSION_MODES:
            raise ValueError(f"未知权限模式: {permission_mode}")

        expanded = expand_mentions(prompt)
        session.messages.append({"role": "user", "content": expanded})
        session.save(session.messages)
        checkpoint = Checkpoint.begin(session_id=session.id, task=prompt, root=self.project_root)
        checkpoint_token = set_current_checkpoint(checkpoint)
        mcp_registry = None if collaboration_mode in {"plan", "review"} else build_mcp_registry(self.project_root)

        resolved_run_id = run_id or uuid.uuid4().hex[:12]
        trace_cfg = self.config.trace
        trace_writer: TraceWriter | None = None
        otel_sink: OtelEventSink | None = None
        if trace_cfg.enabled or trace_cfg.otlp_enabled:
            if trace_cfg.enabled:
                trace_writer = TraceWriter(trace_cfg, resolved_run_id)
            if trace_cfg.otlp_enabled:
                init_otel_provider(trace_cfg)
                otel_sink = OtelEventSink()

        def save() -> None:
            session.save(session.messages)

        sinks: list[EventSink] = []
        if sink is not None:
            sinks.append(sink)
        if otel_sink is not None:
            sinks.append(otel_sink)

        try:
            if mcp_registry is not None:
                mcp_registry.start()
            effective_permission_mode = "read-only" if collaboration_mode in {"plan", "review"} else permission_mode
            with permission_scope(effective_permission_mode), approval_handler(approval):
                result = AgentRunner(
                    sinks=sinks,
                    trace=trace_writer,
                ).run(
                    RunRequest(
                        provider=self.provider,
                        messages=session.messages,
                        max_steps=self.config.max_steps,
                        on_progress=save,
                        context_limit=self.config.context_limit,
                        planning="always" if collaboration_mode == "plan" else ("off" if collaboration_mode == "review" else self.config.planning),
                        planning_max_steps=self.config.planning_max_steps,
                        plan_only=collaboration_mode == "plan",
                        mode_instruction=(REVIEW_INSTRUCTION if collaboration_mode == "review" else None),
                        allowed_tool_names=(READ_ONLY_TOOLS if collaboration_mode == "review" or permission_mode == "read-only" else None),
                        budget_usd=budget_usd,
                        model=self.config.default_model,
                        pricing_overrides=self.config.pricing,
                        run_id=resolved_run_id,
                        cancellation_token=cancellation_token,
                        retry_transient=True,
                        max_retries=self.config.provider.max_retries,
                        retry_backoff=self.config.provider.retry_backoff,
                        context_selector=self.context_selector,
                    )
                )
            save()
            if trace_writer is not None and trace_writer.path is not None:
                result.trace_path = str(trace_writer.path)
            return result
        finally:
            if mcp_registry is not None:
                mcp_registry.stop()
            close_service(self.project_root)
            reset_current_checkpoint(checkpoint_token)
            if trace_writer is not None:
                trace_writer.close()
            if otel_sink is not None:
                otel_sink.close()
            if trace_cfg.otlp_enabled:
                shutdown_otel_provider()

    def session_payload(self, session: Session, *, include_messages: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": session.id,
            "model": session.model,
            "provider": session.provider,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "turns": session.turn_count(),
            "preview": session.first_user_text()[:160],
        }
        if include_messages:
            payload["messages"] = session.messages
        return payload


__all__ = [
    "COLLABORATION_MODES",
    "READ_ONLY_TOOLS",
    "MyCodeRuntime",
    "RuntimeInfo",
]
