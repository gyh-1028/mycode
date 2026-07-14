"""Full-screen Textual interface backed by the shared MyCode runtime."""

from __future__ import annotations

import json
import os
import queue
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar, overload

from rich.markup import escape
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import (
    Button,
    Collapsible,
    DataTable,
    Input,
    Label,
    ListItem,
    ListView,
    Markdown,
    Select,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

from mycode.agent.events import AgentEvent, EventType
from mycode.agent.runner import CancellationToken
from mycode.approvals import ApprovalRequest
from mycode.config import LOCAL_CONFIG_PATH, ConfigError, load_config_result
from mycode.logging_config import redact_log_text
from mycode.runtime import MyCodeRuntime
from mycode.session import Session
from mycode.tui.state import ActivityEntry, TUIState

# Map internal English statuses to Chinese labels shown in the TUI.
_STATUS_LABELS = {
    "idle": "就绪",
    "running": "运行中",
    "completed": "完成",
    "cancelled": "已取消",
    "failed": "失败",
    "max_steps": "达到步数上限",
    "stuck": "卡住",
    "budget_exceeded": "预算超限",
    "model_error": "模型错误",
    "deadline_exceeded": "超时",
}


def _status_label(status: str) -> str:
    return _STATUS_LABELS.get(status, status)


class ApprovalScreen(ModalScreen[bool]):
    """Blocking approval modal resolved back to the worker thread."""

    CSS = """
    ApprovalScreen { align: center middle; background: rgba(2, 6, 9, 0.82); }
    #approval-dialog { width: 76%; max-width: 108; height: 72%; background: #12171c; border: solid #d9a441; padding: 1 2; }
    #approval-title { height: 3; padding: 1 0; text-style: bold; color: #f0bd59; }
    #approval-detail { height: 1fr; border: solid #2b333c; background: #0b0f13; color: #d6dde3; }
    #approval-actions { height: 4; padding-top: 1; align-horizontal: right; }
    #approval-actions Button { min-width: 12; margin-left: 1; border: none; }
    #deny { background: #2a1b20; color: #ff8a9a; }
    #approve { background: #1fae98; color: #061310; text-style: bold; }
    """

    BINDINGS = [
        ("ctrl+k", "reject", "拒绝"),
    ]

    def __init__(self, request: ApprovalRequest) -> None:
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        detail = self.request.diff or self.request.command or self.request.action or ""
        with Vertical(id="approval-dialog"):
            yield Label(self.request.prompt, id="approval-title")
            yield TextArea(detail, read_only=True, show_line_numbers=False, id="approval-detail")
            with Horizontal(id="approval-actions"):
                yield Button("拒绝", id="deny", variant="error")
                yield Button("允许", id="approve", variant="success")

    @on(Button.Pressed)
    def decide(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "approve")

    def action_reject(self) -> None:
        self.dismiss(False)


class ConfigWizardScreen(ModalScreen[MyCodeRuntime | None]):
    """First-run configuration wizard that runs inside the TUI."""

    CSS = """
    ConfigWizardScreen { align: center middle; background: rgba(2, 6, 9, 0.86); }
    #wizard-dialog { width: 74; max-width: 90; height: auto; background: #12171c; border: solid #2dd4bf; padding: 1 3; }
    #wizard-title { height: 3; padding-top: 1; text-style: bold; color: #5eead4; }
    #wizard-error { height: auto; color: $error; margin: 1 0; }
    #wizard-status { height: auto; color: $error; margin: 1 0; }
    .wizard-label { height: 1; margin-top: 1; color: #8b98a5; }
    ConfigWizardScreen Input, ConfigWizardScreen Select { border: solid #303944; background: #0b0f13; }
    ConfigWizardScreen Input:focus, ConfigWizardScreen Select:focus { border: solid #2dd4bf; }
    #wizard-actions { height: 3; align-horizontal: right; margin-top: 1; }
    #wizard-actions Button { min-width: 14; margin-left: 1; }
    """

    _PROVIDERS = ["deepseek", "openai", "anthropic", "kimi"]

    def __init__(self, *, project_root: Path, error_message: str | None = None) -> None:
        super().__init__()
        self.project_root = project_root
        self.error_message = error_message or ""
        self._existing = self._load_existing()

    def _load_existing(self):
        try:
            return load_config_result()
        except ConfigError:
            return None

    @staticmethod
    def _default_model(provider: str) -> str:
        return {
            "deepseek": "deepseek-chat",
            "openai": "gpt-4o-mini",
            "anthropic": "claude-sonnet-4-6",
            "kimi": "kimi-k2.7-code",
        }.get(provider, "deepseek-chat")

    @staticmethod
    def _default_key_env(provider: str) -> str:
        return {
            "deepseek": "DEEPSEEK_API_KEY",
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "kimi": "MOONSHOT_API_KEY",
        }.get(provider, "OPENAI_API_KEY")

    @staticmethod
    def _default_base_url(provider: str) -> str:
        return {
            "deepseek": "https://api.deepseek.com",
            "kimi": "https://api.moonshot.cn/v1",
        }.get(provider, "")

    def compose(self) -> ComposeResult:
        provider = self._existing.config.provider.type if self._existing else "deepseek"
        if provider not in self._PROVIDERS:
            provider = "deepseek"
        with Vertical(id="wizard-dialog"):
            yield Static("欢迎使用 MyCode", id="wizard-title")
            if self.error_message:
                yield Static(self.error_message, id="wizard-error")
            yield Static("Provider", classes="wizard-label")
            yield Select(
                [(p.capitalize(), p) for p in self._PROVIDERS],
                value=provider,
                id="provider-select",
            )
            yield Static("模型", classes="wizard-label")
            yield Input(value=self._default_model(provider), id="model-input")
            yield Static("Base URL（可选）", classes="wizard-label")
            yield Input(value=self._default_base_url(provider), id="base-url-input")
            yield Static("API Key 环境变量名", classes="wizard-label")
            yield Input(value=self._default_key_env(provider), id="key-env-input")
            yield Static("API Key（仅保存到当前进程环境）", classes="wizard-label")
            yield Input(value="", password=True, id="key-input")
            with Horizontal(id="wizard-actions"):
                yield Button("保存并进入", id="wizard-save", variant="success")
                yield Button("退出", id="wizard-quit", variant="error")
            yield Static("", id="wizard-status")

    @on(Select.Changed, "#provider-select")
    def provider_changed(self, event: Select.Changed) -> None:
        provider = str(event.value) if event.value else "deepseek"
        self.query_one("#model-input", Input).value = self._default_model(provider)
        self.query_one("#base-url-input", Input).value = self._default_base_url(provider)
        self.query_one("#key-env-input", Input).value = self._default_key_env(provider)

    @staticmethod
    def _build_toml(provider: str, model: str, base_url: str, api_key_env: str) -> str:
        real_type = "openai" if provider == "deepseek" else provider
        if not base_url:
            base_url = ConfigWizardScreen._default_base_url(provider)
        lines = [
            f'default_model = "{model}"',
            "max_steps = 20",
            'planning = "auto"',
            "planning_max_steps = 5",
            "max_file_lines = 1500",
            "max_command_output = 20000",
            "command_timeout = 60",
            "",
            "[provider]",
            f'type = "{real_type}"',
            f'api_key_env = "{api_key_env}"',
        ]
        if base_url:
            lines.append(f'base_url = "{base_url}"')
        lines.extend(
            [
                "timeout = 60.0",
                "max_retries = 2",
                "retry_backoff = 1.0",
                "stream_usage = true",
                "",
                "[permissions]",
                'write = "ask"',
                'command = "ask"',
            ]
        )
        return "\n".join(lines) + "\n"

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".toml.tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)

    @on(Button.Pressed, "#wizard-save")
    async def save(self) -> None:
        status = self.query_one("#wizard-status", Static)
        provider = str(self.query_one("#provider-select", Select).value)
        model = self.query_one("#model-input", Input).value.strip()
        base_url = self.query_one("#base-url-input", Input).value.strip()
        api_key_env = self.query_one("#key-env-input", Input).value.strip()
        api_key = self.query_one("#key-input", Input).value.strip()

        if not provider or not model or not api_key_env:
            status.update("Provider、模型和 API Key 环境变量名不能为空")
            return
        if not api_key:
            status.update("请输入 API Key；它只保存在当前进程环境中，不会写入文件。")
            return

        toml = self._build_toml(provider, model, base_url, api_key_env)
        try:
            self._atomic_write(LOCAL_CONFIG_PATH, toml)
        except OSError as exc:
            status.update(f"写入配置失败: {exc}")
            return

        os.environ[api_key_env] = api_key

        try:
            runtime = MyCodeRuntime.from_environment(project_root=self.project_root)
        except ConfigError as exc:
            status.update(f"配置验证失败: {exc}")
            return

        status.update("")
        self.dismiss(runtime)

    @on(Button.Pressed, "#wizard-quit")
    def quit(self) -> None:
        self.dismiss(None)


T = TypeVar("T", bound=Widget)


class MyCodeTUI(App[None]):
    """Dense coding workspace driven entirely by AgentEvent projections."""

    TITLE = "MyCode"
    SUB_TITLE = "AI coding workspace"
    CSS = """
    Screen { background: #0b0f10; color: #dce3e8; }

    #topbar { dock: top; height: 3; padding: 0 2; background: #101419; border-bottom: solid #252c33; }
    #brand { width: auto; content-align: left middle; text-style: bold; color: #f0bd59; }
    #workspace-name { width: 1fr; margin-left: 2; content-align: left middle; color: #6f7c87; }
    #status-dot { width: 2; content-align: center middle; color: #74818c; }
    #run-status { width: auto; min-width: 28; content-align: right middle; color: #aeb8c1; }

    #workspace { height: 1fr; }

    #sessions-pane { width: 28; min-width: 24; padding: 0 1; background: #0d1115; border-right: solid #252c33; }
    #sessions-header { height: 4; padding: 1 0; }
    .pane-title { width: 1fr; height: 2; content-align: left middle; text-style: bold; color: #c8d0d6; }
    #session-actions { width: 7; height: 2; align-horizontal: right; }
    #session-actions Button { width: 3; min-width: 3; height: 1; margin-left: 1; padding: 0; border: none; background: transparent; color: #8f9ba5; }
    #session-actions Button:hover { background: #1a2229; color: #2dd4bf; }
    #sessions { height: 1fr; background: transparent; scrollbar-color: #38434d; scrollbar-background: #0d1115; }
    #sessions ListItem { height: 4; margin-bottom: 1; padding: 0 1; border-left: tall transparent; color: #aeb8c1; }
    #sessions ListItem:hover { background: #141a20; }
    #sessions ListItem.--highlight { background: #16231f; border-left: tall #2dd4bf; color: #e7f7f3; }

    #main-pane { width: 1fr; min-width: 42; }
    #transcript { height: 1fr; padding: 1 3 2 3; background: #0b0f10; scrollbar-color: #38434d; scrollbar-background: #0b0f10; }

    .message { height: auto; margin-bottom: 1; padding: 1 2; }
    .user-message { width: 88%; margin-left: 4; background: #13231f; border-left: solid #2dd4bf; }
    .assistant-message { width: 100%; background: #0d1115; border-left: solid #f0bd59; }
    .system-message { width: 100%; background: #21171b; border-left: solid #f06a7d; }
    .message-role { height: 1; text-style: bold; margin-bottom: 1; }
    .message-text { width: 100%; height: auto; }
    .user-role { color: #2dd4bf; }
    .assistant-role { color: #f0bd59; }
    .user-text { color: #e1f1ed; }
    .assistant-reasoning { margin: 0 0 1 0; display: none; }
    .assistant-reasoning .collapsible--title { color: #75828d; text-style: italic; }

    Markdown { background: transparent; color: #dce3e8; }

    #composer-row { height: 8; padding: 1 2; border-top: solid #252c33; background: #0f1317; }
    #composer { width: 1fr; height: 6; border: solid #303944; background: #090c0f; color: #e6edf1; }
    #composer:focus { border: solid #2dd4bf; }
    #composer-actions { width: 11; height: 6; margin-left: 1; }
    #composer-actions Button { width: 100%; height: 3; border: none; }
    #send { background: #2dd4bf; color: #061310; text-style: bold; }
    #send:hover { background: #5eead4; }
    #cancel { background: transparent; color: #f08b99; }
    #cancel:hover { background: #25191d; }

    #details-pane { width: 38; min-width: 32; background: #0d1115; border-left: solid #252c33; }
    #details-header { height: 4; padding: 1 2; }
    #details { height: 1fr; }
    TabbedContent { background: #0d1115; }
    Tabs { height: 3; background: #0d1115; border-bottom: solid #252c33; }
    Tab { padding: 0 2; color: #7f8b95; }
    Tab.-active { color: #2dd4bf; text-style: bold; border-bottom: solid #2dd4bf; }
    #activity-table { height: 1fr; background: #0d1115; color: #b8c2ca; }
    #activity-table > .datatable--header { background: #151b21; color: #8f9ba5; }
    #activity-table > .datatable--even-row { background: #10151a; }
    #activity-table > .datatable--cursor { background: #16352f; color: #e8f7f3; text-style: none; }
    #activity-table:focus > .datatable--cursor { background: #1a453d; color: #f1fffb; text-style: none; }
    #activity-detail { display: none; height: 10; border-top: solid #252c33; background: #090c0f; color: #9faab3; }
    #diff-log { height: 1fr; padding: 1; background: #090c0f; color: #cdd6dd; }
    #metrics { height: 5; padding: 1 2; border-top: solid #252c33; background: #101419; color: #8996a1; }
    """

    BINDINGS = [
        ("ctrl+enter", "submit", "发送"),
        ("ctrl+n", "new_session", "新会话"),
        ("ctrl+k", "cancel_run", "取消"),
        ("ctrl+q", "quit", "退出"),
    ]

    def __init__(
        self,
        runtime: MyCodeRuntime | None = None,
        session_id: str | None = None,
        *,
        config_error: str | None = None,
    ) -> None:
        super().__init__()
        self.runtime = runtime
        self._session_id = session_id
        self._config_error = config_error
        self.state = TUIState(model=runtime.info.model if runtime else "")
        self.session: Session | None = None
        if runtime is not None:
            self.session = runtime.get_session(session_id, persist=False)
            self.state.session_id = self.session.id
        self._token: CancellationToken | None = None
        self._session_ids: list[str] = []
        self._event_queue: queue.Queue[tuple[AgentEvent, dict[str, Any]]] = queue.Queue()
        self._auto_scroll = True
        self._approval_resolve: Callable[[bool], None] | None = None
        self._assistant_markdown: Markdown | None = None
        self._reasoning_collapsible: Collapsible | None = None
        self._reasoning_content: Static | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Static("MYCODE", id="brand")
            yield Static(Path.cwd().name, id="workspace-name")
            yield Static("●", id="status-dot")
            yield Static("就绪", id="run-status")
        with Horizontal(id="workspace"):
            with Vertical(id="sessions-pane"):
                with Horizontal(id="sessions-header"):
                    yield Static("会话", classes="pane-title")
                    with Horizontal(id="session-actions"):
                        new_button = Button("＋", id="new-session")
                        new_button.tooltip = "新建会话"
                        yield new_button
                        refresh_button = Button("↻", id="refresh-sessions")
                        refresh_button.tooltip = "刷新会话"
                        yield refresh_button
                yield ListView(id="sessions")
            with Vertical(id="main-pane"):
                yield VerticalScroll(id="transcript")
                with Horizontal(id="composer-row"):
                    yield TextArea(placeholder="描述你的任务...", show_line_numbers=False, id="composer")
                    with Vertical(id="composer-actions"):
                        yield Button("发送", id="send")
                        yield Button("停止", id="cancel", disabled=True)
            with Vertical(id="details-pane"):
                with Horizontal(id="details-header"):
                    yield Static("运行详情", classes="pane-title")
                with TabbedContent(id="details"):
                    with TabPane("活动", id="activity-tab"):
                        yield DataTable(id="activity-table", show_cursor=True, cursor_type="row")
                        yield TextArea(read_only=True, show_line_numbers=False, id="activity-detail")
                    with TabPane("Diff", id="diff-tab"):
                        yield TextArea(read_only=True, show_line_numbers=False, id="diff-log")
                yield Static(id="metrics")

    async def on_mount(self) -> None:
        self.set_interval(1 / 30, self._drain_event_queue)
        table = self.query_one("#activity-table", DataTable)
        table.add_columns("状态", "活动", "耗时")
        if self.runtime is None:
            self.push_screen(
                ConfigWizardScreen(
                    project_root=Path.cwd().resolve(),
                    error_message=self._config_error,
                ),
                self._on_wizard_result,
            )
        else:
            await self._refresh_sessions()
            if self.session is not None:
                await self._render_session(self.session)
            self._refresh_status()
            self._apply_responsive_layout(self.size.width)
            self.query_one("#composer", TextArea).focus()

    async def _on_wizard_result(self, runtime: MyCodeRuntime | None) -> None:
        if runtime is None:
            self.exit()
            return
        self.runtime = runtime
        self.state.model = runtime.info.model
        self.session = runtime.get_session(self._session_id, persist=False)
        self.state.session_id = self.session.id
        await self._refresh_sessions()
        await self._render_session(self.session)
        self._refresh_status()
        self.query_one("#composer", TextArea).focus()

    @overload
    def _main_query(self, selector: str) -> Widget: ...

    @overload
    def _main_query(self, selector: str, expect_type: type[T]) -> T: ...

    def _main_query(self, selector: str, expect_type: type | None = None):
        """Query the base workspace even while an approval modal is active."""
        screen = self.screen_stack[0]
        if expect_type is None:
            return screen.query_one(selector)
        return screen.query_one(selector, expect_type)  # type: ignore[reportArgumentType]

    def on_resize(self, event: events.Resize) -> None:
        self._apply_responsive_layout(event.size.width)

    def _apply_responsive_layout(self, width: int) -> None:
        sessions = self.query_one("#sessions-pane")
        details = self.query_one("#details-pane")
        sessions.styles.display = "block" if width >= 112 else "none"
        details.styles.display = "block" if width >= 88 else "none"
        sessions.styles.width = 28 if width >= 140 else 24
        details.styles.width = 38 if width >= 140 else 32

    async def _refresh_sessions(self) -> None:
        if self.runtime is None:
            return
        view = self._main_query("#sessions", ListView)
        await view.clear()
        sessions = self.runtime.list_sessions()
        self._session_ids = [session.id for session in sessions]
        for session in sessions:
            preview = session.first_user_text().replace("\n", " ").strip() or "新会话"
            label = f"{preview[:20]}\n[dim]{session.updated_at[5:16]} · {session.turn_count()} 轮[/dim]"
            await view.append(ListItem(Label(label, markup=True)))

    async def _render_session(self, session: Session) -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        await transcript.remove_children()
        self._assistant_markdown = None
        self._reasoning_collapsible = None
        self._reasoning_content = None
        for message in session.messages:
            role = message.get("role")
            content = message.get("content")
            if role == "user" and content:
                await self._add_user_bubble(str(content))
            elif role == "assistant" and content:
                await self._add_assistant_bubble(str(content))
        self.state.session_id = session.id
        self._scroll_to_bottom()

    async def _add_user_bubble(self, text: str) -> None:
        bubble = Vertical(
            Label("你", classes="message-role user-role"),
            Label(text, classes="message-text user-text"),
            classes="message user-message",
        )
        await self.query_one("#transcript", VerticalScroll).mount(bubble)

    async def _add_assistant_bubble(self, text: str = "") -> Markdown:
        reasoning_content = Static("")
        reasoning = Collapsible(
            reasoning_content,
            title="思考",
            collapsed=True,
            classes="assistant-reasoning",
        )
        reasoning.styles.display = "none"
        md = Markdown(text)
        bubble = Vertical(
            Label("MyCode", classes="message-role assistant-role"),
            reasoning,
            md,
            classes="message assistant-message",
        )
        await self.query_one("#transcript", VerticalScroll).mount(bubble)
        self._assistant_markdown = md
        self._reasoning_collapsible = reasoning
        self._reasoning_content = reasoning_content
        return md

    async def _ensure_assistant_bubble(self) -> Markdown:
        if self._assistant_markdown is None:
            await self._add_assistant_bubble()
        assert self._assistant_markdown is not None
        return self._assistant_markdown

    def _scroll_to_bottom(self) -> None:
        if self._auto_scroll:
            self.query_one("#transcript", VerticalScroll).scroll_end(animate=False)

    def _check_auto_scroll(self) -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        try:
            at_bottom = transcript.scroll_y >= transcript.max_scroll_y - 1
        except AttributeError:
            at_bottom = True
        self._auto_scroll = at_bottom

    def on_scroll(self) -> None:
        self._check_auto_scroll()

    @on(ListView.Selected, "#sessions")
    async def select_session(self, event: ListView.Selected) -> None:
        if self.state.status == "running" or event.list_view.index is None or self.runtime is None:
            return
        index = int(event.list_view.index)
        if index >= len(self._session_ids):
            return
        session = self.runtime.get_session(self._session_ids[index])
        self.session = session
        await self._render_session(session)
        self._refresh_status()

    @on(Button.Pressed, "#send")
    async def send_pressed(self) -> None:
        await self.action_submit()

    @on(Button.Pressed, "#cancel")
    def cancel_pressed(self) -> None:
        self.action_cancel_run()

    @on(Button.Pressed, "#new-session")
    async def new_session_pressed(self) -> None:
        await self.action_new_session()

    @on(Button.Pressed, "#refresh-sessions")
    async def refresh_sessions_pressed(self) -> None:
        await self._refresh_sessions()

    async def on_key(self, event: events.Key) -> None:
        if event.key == "ctrl+enter":
            event.prevent_default()
            event.stop()
            await self.action_submit()

    async def action_submit(self) -> None:
        if self.state.status == "running" or self.runtime is None or self.session is None:
            return
        composer = self.query_one("#composer", TextArea)
        prompt = composer.text.strip()
        if not prompt:
            self.notify("请输入任务", severity="warning")
            return
        composer.clear()
        self._assistant_markdown = None
        self._reasoning_collapsible = None
        self._reasoning_content = None
        await self._add_user_bubble(prompt)
        await self._add_assistant_bubble()
        self.state.begin_user_turn()
        self._token = CancellationToken()
        self.query_one("#send", Button).disabled = True
        self.query_one("#cancel", Button).disabled = False
        self._refresh_status()
        self._scroll_to_bottom()
        self._run_prompt(prompt)

    @work(thread=True, exclusive=True, group="agent-run")
    def _run_prompt(self, prompt: str) -> None:
        runtime = self.runtime
        if runtime is None:
            self.call_from_thread(self._run_complete, "failed", "TUI runtime not initialized")
            return
        session = self.session
        if session is None:
            self.call_from_thread(self._run_complete, "failed", "TUI session not initialized")
            return
        try:
            result = runtime.run_prompt(
                session,
                prompt,
                sink=self._event_sink,
                cancellation_token=self._token,
                approval=self._approval_handler,
            )
            self.call_from_thread(self._run_complete, result.status, result.error)
        except Exception as exc:  # noqa: BLE001 - frontend must remain alive
            self.call_from_thread(self._run_complete, "failed", f"{type(exc).__name__}: {exc}")

    def _event_sink(self, event: AgentEvent, attachments: dict[str, Any]) -> None:
        self._event_queue.put((event, attachments))

    async def _drain_event_queue(self) -> None:
        """Apply queued events on the main thread while preserving order."""
        processed = False
        while True:
            try:
                event, attachments = self._event_queue.get_nowait()
            except queue.Empty:
                break
            await self._apply_event(event, attachments)
            processed = True
        if processed:
            self._scroll_to_bottom()

    def _approval_handler(self, request: ApprovalRequest) -> bool:
        completed = threading.Event()
        answer = [False]

        def resolved(value: bool) -> None:
            answer[0] = bool(value)
            completed.set()

        self.call_from_thread(self._show_approval, request, resolved)
        completed.wait()
        return answer[0]

    def _show_approval(self, request: ApprovalRequest, resolved: Callable[[bool], None]) -> None:
        self._approval_resolve = resolved
        if request.diff:
            self.state.last_diff = request.diff
            diff_log = self._main_query("#diff-log", TextArea)
            diff_log.load_text(request.diff)
            self._main_query("#details", TabbedContent).active = "diff-tab"
        self.push_screen(ApprovalScreen(request), self._on_approval_closed)

    def _on_approval_closed(self, result: bool | None) -> None:
        resolve = self._approval_resolve
        self._approval_resolve = None
        if resolve is not None:
            resolve(bool(result))

    def _reject_pending_approval(self) -> None:
        resolve = self._approval_resolve
        self._approval_resolve = None
        if resolve is not None:
            resolve(False)
        if isinstance(self.screen, ApprovalScreen):
            self.pop_screen()

    async def _apply_event(self, event: AgentEvent, attachments: dict[str, Any]) -> None:
        self.state.apply(event, attachments)

        if event.type == EventType.MODEL_STREAM_TEXT:
            md = await self._ensure_assistant_bubble()
            md.update(self.state.streamed_text)
        elif event.type == EventType.MODEL_STREAM_REASONING:
            await self._ensure_assistant_bubble()
            if self._reasoning_content is not None and self._reasoning_collapsible is not None:
                self._reasoning_content.update(self.state.reasoning_text)
                self._reasoning_collapsible.styles.display = "block"
        elif event.type in {
            EventType.PLAN_CREATED,
            EventType.MODEL_CALL_STARTED,
            EventType.TOOL_CALL_STARTED,
            EventType.TOOL_CALL_FINISHED,
            EventType.MODEL_STREAM_END,
            EventType.MODEL_CALL_ERROR,
        }:
            self._refresh_activity()

        if self.state.last_diff:
            diff_log = self._main_query("#diff-log", TextArea)
            diff_log.load_text(self.state.last_diff)

        if event.type in {EventType.RUN_FAILED, EventType.RUN_STUCK, EventType.RUN_MAX_STEPS, EventType.RUN_BUDGET_EXCEEDED, EventType.MODEL_CALL_ERROR}:
            detail = self.state.error or str(event.payload.get("detail", ""))
            if detail:
                await self._add_system_bubble(detail, severity="error")
        elif event.type == EventType.RUN_CANCELLED:
            await self._add_system_bubble("运行已取消", severity="warning")

        self._refresh_status()

    async def _add_system_bubble(self, text: str, severity: str = "information") -> None:
        color = "red" if severity == "error" else "yellow" if severity == "warning" else "cyan"
        bubble = Vertical(
            Label(f"[{color}]{escape(text)}[/{color}]", markup=True),
            classes="message system-message",
        )
        await self.query_one("#transcript", VerticalScroll).mount(bubble)

    def _refresh_activity(self) -> None:
        table = self._main_query("#activity-table", DataTable)
        table.clear()
        for entry in self.state.activity_entries:
            if entry.status == "running":
                status_icon = "●"
            elif entry.is_error:
                status_icon = "✗"
            else:
                status_icon = "✓"
            duration = f"{entry.duration_ms}ms" if entry.duration_ms is not None else ""
            table.add_row(status_icon, entry.title, duration, key=entry.id)

    @on(DataTable.RowSelected, "#activity-table")
    def _show_activity_detail(self, event: DataTable.RowSelected) -> None:
        row_index = event.cursor_row
        if row_index is None or row_index < 0 or row_index >= len(self.state.activity_entries):
            return
        entry = self.state.activity_entries[row_index]
        detail = self._format_activity_detail(entry)
        detail_view = self._main_query("#activity-detail", TextArea)
        detail_view.load_text(detail)
        detail_view.styles.display = "block"

    def _format_activity_detail(self, entry: ActivityEntry) -> str:
        lines: list[str] = []
        lines.append(f"类型: {entry.kind}")
        lines.append(f"名称: {entry.title}")
        lines.append(f"状态: {entry.status}{' (错误)' if entry.is_error else ''}")
        if entry.duration_ms is not None:
            lines.append(f"耗时: {entry.duration_ms}ms")
        if entry.subtitle:
            lines.append(f"摘要: {redact_log_text(entry.subtitle)}")
        if entry.detail:
            lines.append("")
            lines.append(redact_log_text(entry.detail))
        if entry.kind == "tool":
            if entry.tool_args is not None:
                lines.append("")
                lines.append("参数:")
                lines.append(redact_log_text(json.dumps(entry.tool_args, ensure_ascii=False, indent=2)))
            if entry.tool_result is not None:
                lines.append("")
                lines.append("输出:")
                result_text = entry.tool_result if isinstance(entry.tool_result, str) else json.dumps(entry.tool_result, ensure_ascii=False, indent=2)
                lines.append(redact_log_text(result_text[:20000]))
        return "\n".join(lines)

    def _run_complete(self, status: str, error: str | None) -> None:
        self.state.status = status
        if error:
            self.state.error = error
        self._token = None
        try:
            self._main_query("#send", Button).disabled = False
            self._main_query("#cancel", Button).disabled = True
            self._refresh_status()
            self.call_later(self._refresh_sessions)
            self._main_query("#composer", TextArea).focus()
        except NoMatches:
            # The test/terminal may be shutting down while a worker posts its
            # final callback. Business state is already persisted by Runtime.
            return

    def _refresh_status(self) -> None:
        if self.runtime is None or self.session is None:
            return
        status = self.state.status
        status_color = {
            "running": "#f0bd59",
            "completed": "#2dd4bf",
            "idle": "#74818c",
            "cancelled": "#f0bd59",
            "max_steps": "#f0bd59",
            "stuck": "#f0bd59",
            "budget_exceeded": "#f0bd59",
            "failed": "#f06a7d",
            "model_error": "#f06a7d",
            "deadline_exceeded": "#f06a7d",
        }.get(status, "#74818c")
        self._main_query("#status-dot", Static).styles.color = status_color
        self._main_query("#run-status", Static).update(
            f"{_status_label(status)}   {self.runtime.info.model}   #{self.session.id[-6:]}"
        )
        cost = "--" if self.state.estimated_cost is None else f"${self.state.estimated_cost:.6f}"
        self._main_query("#metrics", Static).update(
            f"模型  {self.runtime.info.model}\n"
            f"Token  {self.state.prompt_tokens} 输入  {self.state.completion_tokens} 输出\n"
            f"缓存  {self.state.cached_tokens}    成本  {cost}"
        )

    async def action_new_session(self) -> None:
        if self.state.status == "running" or self.runtime is None:
            return
        self.session = self.runtime.new_session(persist=False)
        self.state = TUIState(session_id=self.session.id, model=self.runtime.info.model)
        self._assistant_markdown = None
        self._reasoning_collapsible = None
        self._reasoning_content = None
        await self._render_session(self.session)
        await self._refresh_sessions()
        self._refresh_status()
        self.query_one("#composer", TextArea).focus()

    def action_cancel_run(self) -> None:
        if self._token is not None:
            self._token.cancel()
            self.notify("正在安全取消运行")
        self._reject_pending_approval()

    async def action_quit(self) -> None:
        self._reject_pending_approval()
        if self._token is not None:
            self._token.cancel()
        await super().action_quit()


def run_tui(session_id: str | None = None) -> None:
    project_root = Path.cwd().resolve()
    config_error: str | None = None
    runtime: MyCodeRuntime | None = None
    try:
        runtime = MyCodeRuntime.from_environment(project_root=project_root)
    except ConfigError as exc:
        config_error = str(exc)
    app = MyCodeTUI(runtime=runtime, session_id=session_id, config_error=config_error)
    try:
        app.run()
    finally:
        active_runtime = app.runtime
        if active_runtime is not None:
            close = getattr(active_runtime, "close", None)
            if callable(close):
                close()


__all__ = ["ApprovalScreen", "ConfigWizardScreen", "MyCodeTUI", "run_tui"]
