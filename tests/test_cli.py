"""CLI tests: Task 0 (shell + --help) and Task 1 (task echo + config/key)."""

import json
from dataclasses import dataclass
from pathlib import Path

from typer.testing import CliRunner

from mycode import __version__
from mycode.agent.events import RunStatus
from mycode.agent.runner import AgentRunResult
from mycode.checkpoint import Checkpoint
from mycode.cli import app
from mycode.config import Config, ConfigLoadResult, load_config_result
from mycode.llm.base import LLMResponse
from mycode.runtime import RuntimeInfo
from mycode.session import Session

runner = CliRunner()


def _combined(result) -> str:
    """stdout + stderr, robust to Click versions that separate the two."""
    out = result.stdout or ""
    try:
        err = result.stderr or ""
    except (ValueError, RuntimeError):
        err = ""  # this Click merges stderr into stdout already
    return out + err


def _patch_runtime(monkeypatch, *, answer: str = "stub answer"):
    """Replace MyCodeRuntime used by the CLI with a hermetic fake.

    The fake records the calls to run_prompt and persists sessions like the
    real runtime, but never touches the network.
    """
    calls: list[dict] = []

    @dataclass
    class FakeRuntime:
        config_result: ConfigLoadResult
        project_root: str
        config: Config
        info: RuntimeInfo

        @classmethod
        def from_environment(cls, project_root=None):
            return cls.from_config_result(load_config_result(), project_root=project_root)

        @classmethod
        def from_config_result(cls, config_result: ConfigLoadResult, project_root=None):
            return cls(config_result, project_root or Path.cwd())

        def __init__(self, config_result: ConfigLoadResult, project_root) -> None:
            self.config_result = config_result
            self.config = config_result.config
            self.project_root = project_root
            self.info = RuntimeInfo(
                project_root=project_root,
                model=self.config.default_model,
                provider=self.config.provider.type or "openai",
                config_sources=tuple(str(p) for p in config_result.files),
            )

        def new_session(self, *, persist: bool = True) -> Session:
            messages = [{"role": "system", "content": "你是 mycode"}]
            session = Session.new(
                model=self.config.default_model,
                provider=self.config.provider.type or "openai",
                messages=messages,
            )
            if persist:
                session.save(session.messages)
            return session

        def get_session(self, session_id: str | None = None, *, persist: bool = True) -> Session:
            if session_id:
                session = Session.load(session_id)
                if session is None:
                    raise LookupError(f"找不到会话:{session_id}")
                return session
            return self.new_session(persist=persist)

        def list_sessions(self) -> list[Session]:
            return Session.list_all()

        def run_prompt(self, session: Session, prompt: str, *, budget_usd=None, **kwargs) -> AgentRunResult:
            session.messages.append({"role": "user", "content": prompt})
            calls.append(
                {
                    "session": session,
                    "prompt": prompt,
                    "messages": [dict(m) for m in session.messages],
                    "budget_usd": budget_usd,
                    "config": self.config,
                }
            )
            session.messages.append({"role": "assistant", "content": answer})
            session.save(session.messages)
            return AgentRunResult(
                status=RunStatus.COMPLETED,
                final_text=answer,
                run_id="r-fake",
                events=[],
            )

    monkeypatch.setattr("mycode.cli.MyCodeRuntime", FakeRuntime)
    return calls


def test_app_is_callable() -> None:
    # The console-script entry point ``mycode = mycode.cli:app`` requires the
    # Typer app object itself to be callable.
    assert callable(app)


def test_help_exits_zero() -> None:
    result = runner.invoke(app, ["--help"], prog_name="mycode")
    assert result.exit_code == 0
    assert "mycode" in result.output
    assert "TASK" in result.output


def test_no_args_enters_repl(monkeypatch, tmp_path) -> None:
    # No task -> interactive REPL. With an immediate "exit", run_prompt is never
    # called and we leave cleanly.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    calls = _patch_runtime(monkeypatch)
    result = runner.invoke(app, [], prog_name="mycode", input="exit\n")
    assert result.exit_code == 0
    assert "交互模式" in result.output
    assert len(calls) == 0


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"], prog_name="mycode")
    assert result.exit_code == 0
    assert __version__ in result.output


def test_runs_agent_with_task_and_model_header(monkeypatch, tmp_path) -> None:
    # Hermetic: empty cwd (no .mycode/config.toml) -> default config, key set.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    calls = _patch_runtime(monkeypatch, answer="stub answer")

    result = runner.invoke(app, ["你好"], prog_name="mycode")
    assert result.exit_code == 0
    # header still shows the task and the active model
    assert "你好" in result.output
    assert Config().default_model in result.output
    # UI surfaces the security disclaimer
    assert "沙箱" in result.output
    # the runtime was actually invoked with our task + a system prompt
    assert len(calls) == 1
    assert calls[0]["config"].max_steps == Config().max_steps
    assert calls[0]["config"].planning == Config().planning
    assert calls[0]["config"].planning_max_steps == Config().planning_max_steps
    roles = [m["role"] for m in calls[0]["messages"]]
    assert "system" in roles
    assert any(m["role"] == "user" and "你好" in m["content"] for m in calls[0]["messages"])


def test_missing_key_friendly_error(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = runner.invoke(app, ["你好"], prog_name="mycode")
    assert result.exit_code == 1
    combined = _combined(result)
    assert "OPENAI_API_KEY" in combined
    assert "环境变量" in combined


def test_repl_preserves_context_across_turns(monkeypatch, tmp_path) -> None:
    # Two turns in one session; the second must see the first turn's history.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    calls = _patch_runtime(monkeypatch, answer="好的")

    result = runner.invoke(
        app, [], prog_name="mycode", input="改一下 app.py\n刚才那个文件再加个注释\nexit\n"
    )
    assert result.exit_code == 0
    assert len(calls) == 2

    # turn 1: last message is the first user input
    assert calls[0]["messages"][-1] == {"role": "user", "content": "改一下 app.py"}

    # turn 2 still carries turn-1 context (same growing messages list, not rebuilt)
    turn2 = calls[1]["messages"]
    contents = [(m["role"], m["content"]) for m in turn2]
    assert ("user", "改一下 app.py") in contents
    assert ("assistant", "好的") in contents
    assert turn2[-1] == {"role": "user", "content": "刚才那个文件再加个注释"}
    assert len(turn2) > len(calls[0]["messages"])  # history grew, not reset


def test_repl_exit_word_quits_without_running(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    calls = _patch_runtime(monkeypatch)
    result = runner.invoke(app, [], prog_name="mycode", input="quit\n")
    assert result.exit_code == 0
    assert len(calls) == 0


# --------------------------------------------------------------------------- #
# session persistence + resume (Task 12)
# --------------------------------------------------------------------------- #
def test_sessions_listing_via_bare_word_and_flag(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    s = Session.new(model="deepseek-chat", provider="p")
    s.save([{"role": "user", "content": "修复登录 bug"}])

    # `mycode sessions` (no key needed — listing returns before key check)
    out1 = runner.invoke(app, ["sessions"], prog_name="mycode").output
    assert s.id in out1
    assert "修复登录 bug" in out1

    # `mycode --sessions`
    out2 = runner.invoke(app, ["--sessions"], prog_name="mycode").output
    assert s.id in out2


def test_oneshot_saves_session(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _patch_runtime(monkeypatch, answer="好的,已完成")
    result = runner.invoke(app, ["加个 /health"], prog_name="mycode")
    assert result.exit_code == 0

    sessions = Session.list_all()
    assert len(sessions) == 1
    roles_contents = [(m["role"], m["content"]) for m in sessions[0].messages]
    assert ("user", "加个 /health") in roles_contents
    assert ("assistant", "好的,已完成") in roles_contents


def test_continue_restores_latest_history(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    prior = Session.new(model="deepseek-chat", provider="p")
    prior.save(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "改 app.py"},
            {"role": "assistant", "content": "改好了"},
        ]
    )

    calls = _patch_runtime(monkeypatch, answer="ok")
    result = runner.invoke(app, ["--continue", "再加个注释"], prog_name="mycode")
    assert result.exit_code == 0

    contents = [(m["role"], m["content"]) for m in calls[0]["messages"]]
    assert ("user", "改 app.py") in contents       # prior history restored
    assert ("assistant", "改好了") in contents
    assert contents[-1] == ("user", "再加个注释")    # new turn appended


def test_resume_by_id_and_unknown_id(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    prior = Session.new(model="deepseek-chat", provider="p")
    prior.save([{"role": "system", "content": "sys"}, {"role": "user", "content": "第一轮"}])

    calls = _patch_runtime(monkeypatch)
    ok = runner.invoke(app, ["--resume", prior.id, "继续"], prog_name="mycode")
    assert ok.exit_code == 0
    assert ("user", "第一轮") in [(m["role"], m["content"]) for m in calls[0]["messages"]]

    bad = runner.invoke(app, ["--resume", "no-such-id", "x"], prog_name="mycode")
    assert bad.exit_code == 1
    assert "找不到会话" in _combined(bad)


def test_init_writes_project_config(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "deepseek"], prog_name="mycode")

    assert result.exit_code == 0
    cfg = tmp_path / ".mycode" / "config.toml"
    assert cfg.is_file()
    text = cfg.read_text(encoding="utf-8")
    assert "DEEPSEEK_API_KEY" in text
    assert "deepseek-v4-flash" in text


def test_doctor_reports_config_and_missing_key(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".mycode").mkdir()
    (tmp_path / ".mycode" / "config.toml").write_text(
        'default_model = "m"\n[provider]\napi_key_env = "MISSING_TEST_KEY"\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("MISSING_TEST_KEY", raising=False)

    result = runner.invoke(app, ["doctor"], prog_name="mycode")

    assert result.exit_code == 1
    assert "MISSING_TEST_KEY" in result.output
    assert "配置来源" in result.output
    assert "模型目录: v1" in result.output
    assert "当前模型价格: 未知" in result.output


def test_config_show_masks_api_key_value(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-print")

    result = runner.invoke(app, ["config", "show"], prog_name="mycode")

    assert result.exit_code == 0
    assert "api_key:已设置" in result.output
    assert "sk-should-not-print" not in result.output
    assert "default_model" in result.output
    assert "planning:auto" in result.output
    assert "planning_max_steps:5" in result.output


def test_cli_passes_planning_config_to_run_prompt(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    (tmp_path / ".mycode").mkdir()
    (tmp_path / ".mycode" / "config.toml").write_text(
        'planning = "always"\nplanning_max_steps = 3\n',
        encoding="utf-8",
    )
    calls = _patch_runtime(monkeypatch)

    result = runner.invoke(app, ["fix failing tests"], prog_name="mycode")

    assert result.exit_code == 0
    assert calls[0]["config"].planning == "always"
    assert calls[0]["config"].planning_max_steps == 3
    assert calls[0]["messages"][-1] == {"role": "user", "content": "fix failing tests"}


def test_cli_passes_budget_to_run_prompt(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    calls = _patch_runtime(monkeypatch)

    result = runner.invoke(app, ["--budget", "0.5", "fix failing tests"], prog_name="mycode")

    assert result.exit_code == 0
    assert calls[0]["budget_usd"] == 0.5
    assert calls[0]["config"].default_model == Config().default_model


def test_cli_undo_restores_latest_checkpoint_without_api_key(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    target = tmp_path / "app.py"
    target.write_text("new\n", encoding="utf-8")
    session = Session.new(model="m", provider="p")
    session.save([{"role": "user", "content": "edit"}])
    checkpoint = Checkpoint.begin(session_id=session.id, task="edit")
    checkpoint.record_write(target, "old\n", existed=True)

    result = runner.invoke(app, ["--undo"], prog_name="mycode")

    assert result.exit_code == 0
    assert "已撤销检查点" in result.output
    assert target.read_text(encoding="utf-8") == "old\n"


def test_config_where_reports_sources(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".mycode").mkdir()
    (tmp_path / ".mycode" / "config.toml").write_text(
        'default_model = "local"\n', encoding="utf-8"
    )

    result = runner.invoke(app, ["config", "where"], prog_name="mycode")

    assert result.exit_code == 0
    assert "加载顺序" in result.output
    assert "实际加载" in result.output
    assert ".mycode" in result.output


def test_doctor_api_success(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    class Provider:
        def chat(self, messages, tools=None):
            return LLMResponse(text="pong")

    monkeypatch.setattr("mycode.cli.build_provider", lambda cfg, key: Provider())

    result = runner.invoke(app, ["doctor", "--api"], prog_name="mycode")

    assert result.exit_code == 0
    assert "API 连通性:成功" in result.output


def test_doctor_api_failure(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    class Provider:
        def chat(self, messages, tools=None):
            raise RuntimeError("no route")

    monkeypatch.setattr("mycode.cli.build_provider", lambda cfg, key: Provider())

    result = runner.invoke(app, ["doctor", "--api"], prog_name="mycode")

    assert result.exit_code == 1
    assert "API 连通性:失败" in result.output


def test_session_show_and_export(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    s = Session.new(model="m", provider="p")
    s.save([{"role": "user", "content": "第一轮"}])

    show = runner.invoke(app, ["session", "show", s.id], prog_name="mycode")
    assert show.exit_code == 0
    assert s.id in show.output
    assert "turns:1" in show.output

    json_out = tmp_path / "session.json"
    exported = runner.invoke(
        app,
        ["session", "export", s.id, "--format", "json", "--output", str(json_out)],
        prog_name="mycode",
    )
    assert exported.exit_code == 0
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["id"] == s.id
    assert payload["messages"][0]["content"] == "第一轮"

    md_out = tmp_path / "session.md"
    exported_md = runner.invoke(
        app,
        ["session", "export", s.id, "--format", "markdown", "--output", str(md_out)],
        prog_name="mycode",
    )
    assert exported_md.exit_code == 0
    assert f"# mycode session {s.id}" in md_out.read_text(encoding="utf-8")


def test_session_delete_and_prune(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    first = Session.new(model="m", provider="p")
    first.save([{"role": "user", "content": "one"}])
    second = Session.new(model="m", provider="p")
    second.save([{"role": "user", "content": "two"}])

    no = runner.invoke(app, ["session", "delete", first.id], prog_name="mycode", input="n\n")
    assert no.exit_code == 0
    assert first.path.exists()

    yes = runner.invoke(app, ["session", "delete", first.id], prog_name="mycode", input="y\n")
    assert yes.exit_code == 0
    assert not first.path.exists()

    third = Session.new(model="m", provider="p")
    third.save([{"role": "user", "content": "three"}])
    pruned = runner.invoke(
        app, ["session", "prune", "--keep", "1"], prog_name="mycode", input="y\n"
    )
    assert pruned.exit_code == 0
    assert len(Session.list_all()) == 1


def test_repl_slash_commands_do_not_run_agent(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    calls = _patch_runtime(monkeypatch)

    result = runner.invoke(
        app,
        [],
        prog_name="mycode",
        input="/help\n/model\n/sessions\n/doctor\n/exit\n",
    )

    assert result.exit_code == 0
    assert "可用命令" in result.output
    assert "model:" in result.output
    assert "API Key:已设置" in result.output
    assert len(calls) == 0


def test_repl_custom_slash_command_renders_template(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    commands = tmp_path / ".mycode" / "commands"
    commands.mkdir(parents=True)
    (commands / "explain.md").write_text(
        "---\ndescription: explain target\n---\nExplain this: $ARGS\n",
        encoding="utf-8",
    )
    calls = _patch_runtime(monkeypatch, answer="ok")

    result = runner.invoke(app, [], prog_name="mycode", input="/help\n/explain src/app.py\n/exit\n")

    assert result.exit_code == 0
    assert "/explain - explain target" in result.output
    assert calls[-1]["messages"][-1]["content"] == "Explain this: src/app.py"


def test_web_command_forwards_port_and_no_open(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "mycode.web.run_web_server",
        lambda **kwargs: captured.update(kwargs),
    )

    result = runner.invoke(app, ["web", "--port", "8765", "--no-open"], prog_name="mycode")

    assert result.exit_code == 0
    assert captured == {"port": 8765, "open_browser": False}
