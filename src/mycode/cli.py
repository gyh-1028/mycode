"""Command-line entry point for mycode.

A single Typer command. ``mycode "<task>"`` runs one task and exits (one-shot);
``mycode`` with no task drops into an interactive REPL. Conversations are
archived to ``.mycode/sessions/<id>.json`` after each consistent step, so:

- ``mycode --resume <id>``    continue a specific session
- ``mycode --continue``       continue the most recent session
- ``mycode sessions`` / ``--sessions``  list saved sessions

(``sessions`` can't be a real subcommand because the bare positional ``task``
would shadow it, so it's handled as a keyword / flag.)
"""

import json
import os
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import typer

from mycode import __version__
from mycode.checkpoint import Checkpoint
from mycode.commands import SlashCommand, load_commands
from mycode.config import (
    GLOBAL_CONFIG_PATH,
    LOCAL_CONFIG_PATH,
    ConfigError,
    ConfigLoadResult,
    config_with_trace_overrides,
    load_config_result,
    preset_config,
)
from mycode.git_ops import (
    GitState,
    auto_commit_checkpoint,
    prepare_auto_commit,
    render_diff_for_checkpoint,
)
from mycode.llm import build_provider
from mycode.logging_config import setup_logging
from mycode.model_store import (
    PRESETS,
    CredentialStore,
    ModelProfile,
    ModelStore,
    ModelStoreError,
    profile_from_preset,
)
from mycode.permissions import SECURITY_NOTICE
from mycode.plugins import format_plugin_list, list_discovered_plugins
from mycode.runtime import MyCodeRuntime
from mycode.session import Session, SessionError
from mycode.skills import discover_skills, format_skill_list
from mycode.trace import replay


def _ensure_utf8_stdio() -> None:
    """在 Windows 下强制使用 UTF-8，避免中文乱码。

    - Python 的 stdout/stderr 在非 UTF-8 时重配为 utf-8（无论是否终端）。
    - 仅当 stdout 是真实控制台时，才通过 Win32 API 把控制台代码页设为 CP_UTF8。
    这样既能修复 Windows PowerShell 5 / cmd 的终端乱码，又不会破坏重定向到
    文件的编码约定。
    """
    if sys.platform != "win32":
        return

    is_tty = sys.stdout is not None and sys.stdout.isatty()
    if is_tty:
        try:
            import ctypes

            CP_UTF8 = 65001
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleOutputCP(CP_UTF8)
            kernel32.SetConsoleCP(CP_UTF8)
        except Exception:  # noqa: S110 - 控制台设置失败不应阻断 CLI 启动
            pass

    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                current = getattr(stream, "encoding", "")
                if current and current.lower() not in ("utf-8", "utf_8"):
                    stream.reconfigure(encoding="utf-8")  # type: ignore[reportAttributeAccessIssue]
            except Exception:  # noqa: S110 - 编码设置失败不应阻断 CLI 启动
                pass


_ensure_utf8_stdio()


app = typer.Typer(
    add_completion=False,
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)

_EXIT_WORDS = {"exit", "quit", ":q"}
_BUILTIN_SLASH = {"/help", "/sessions", "/doctor", "/model", "/undo", "/diff", "/exit"}
_REPL_HELP = (
    "可用命令:/help /sessions /doctor /model /undo /diff /exit\n"
    "直接输入自然语言任务即可让 mycode 继续处理当前会话。"
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"mycode {__version__}")
        raise typer.Exit()


def _print_sessions() -> None:
    sessions = Session.list_all()
    if not sessions:
        typer.echo("(暂无会话)")
        return
    typer.echo(f"会话列表(共 {len(sessions)} 个,按最近更新排序):")
    for s in sessions:
        preview = s.first_user_text().replace("\n", " ")
        if len(preview) > 40:
            preview = preview[:40] + "…"
        typer.echo(
            f"  {s.id}  [{s.model}]  {s.updated_at}  {s.turn_count()}轮  {preview}"
        )
    typer.echo("\n用 mycode --resume <id> 恢复,或 mycode --continue 接最近一次。")


def _handle_undo(session_id: str | None = None) -> None:
    if session_id is None:
        session = Session.latest()
        session_id = session.id if session is not None else None
    if session_id is None:
        typer.secho("没有可撤销的会话。", fg=typer.colors.YELLOW)
        return
    checkpoint = Checkpoint.latest(session_id)
    if checkpoint is None:
        typer.secho(f"会话 {session_id} 没有可撤销的检查点。", fg=typer.colors.YELLOW)
        return
    result = checkpoint.undo()
    if result.startswith("错误:"):
        typer.secho(result, fg=typer.colors.RED)
    else:
        typer.secho(result, fg=typer.colors.GREEN)


def _print_repl_help(commands: dict[str, SlashCommand]) -> None:
    typer.echo(_REPL_HELP)
    visible = [cmd for cmd in commands.values() if not cmd.shadowed]
    if visible:
        typer.echo("\n自定义命令:")
        for cmd in sorted(visible, key=lambda c: c.name):
            desc = f" - {cmd.description}" if cmd.description else ""
            typer.echo(f"/{cmd.name}{desc}")
    shadowed = [cmd for cmd in commands.values() if cmd.shadowed]
    for cmd in sorted(shadowed, key=lambda c: c.name):
        typer.echo(f"警告:自定义命令 /{cmd.name} 与内置命令冲突,已忽略。")


def _session_payload(session: Session) -> dict[str, Any]:
    return {
        "id": session.id,
        "model": session.model,
        "provider": session.provider,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "messages": session.messages,
    }


def _render_session_markdown(session: Session) -> str:
    lines = [
        f"# mycode session {session.id}",
        "",
        f"- model: {session.model}",
        f"- provider: {session.provider}",
        f"- created_at: {session.created_at}",
        f"- updated_at: {session.updated_at}",
        f"- turns: {session.turn_count()}",
        "",
        "## Messages",
        "",
    ]
    for idx, message in enumerate(session.messages, start=1):
        role = message.get("role", "")
        content = message.get("content")
        lines.append(f"### {idx}. {role}")
        lines.append("")
        if content:
            lines.append(str(content))
            lines.append("")
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function", {})
            lines.append(
                f"- tool_call `{tc.get('id', '')}`: {fn.get('name', '')}({fn.get('arguments', '')})"
            )
        if role == "tool":
            lines.append(f"- tool_call_id: `{message.get('tool_call_id', '')}`")
        if lines[-1] != "":
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _parse_option_value(args: list[str], name: str, default: str | None = None) -> str | None:
    prefix = name + "="
    for idx, arg in enumerate(args):
        if arg == name:
            return args[idx + 1] if idx + 1 < len(args) else None
        if arg.startswith(prefix):
            return arg.split("=", 1)[1]
    return default


def _has_flag(args: list[str], name: str) -> bool:
    return name in args


def _remove_option(args: list[str], name: str) -> list[str]:
    out: list[str] = []
    skip = False
    prefix = name + "="
    for arg in args:
        if skip:
            skip = False
            continue
        if arg == name:
            skip = True
            continue
        if arg.startswith(prefix):
            continue
        out.append(arg)
    return out


def _parse_init_args(args: list[str]) -> tuple[str, bool, bool]:
    provider = "deepseek"
    global_config = False
    force = False
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--global":
            global_config = True
            i += 1
        elif arg == "--force":
            force = True
            i += 1
        elif arg == "--provider":
            if i + 1 >= len(args):
                raise ConfigError("init 缺少 --provider 的取值")
            provider = args[i + 1]
            i += 2
        elif arg.startswith("--provider="):
            provider = arg.split("=", 1)[1]
            i += 1
        elif arg.startswith("-"):
            raise ConfigError(f"init 不认识该选项:{arg}")
        else:
            provider = arg
            i += 1
    return provider, global_config, force


def _handle_init(args: list[str]) -> None:
    try:
        provider, global_config, force = _parse_init_args(args)
        content = preset_config(provider)
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    target = GLOBAL_CONFIG_PATH if global_config else LOCAL_CONFIG_PATH
    if target.exists() and not force:
        typer.secho(
            f"配置文件已存在:{target}。如需覆盖,加 --force。",
            fg=typer.colors.YELLOW,
        )
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    typer.secho(f"已生成配置:{target}", fg=typer.colors.GREEN)

    cfg = load_config_result(target).config
    typer.echo(f"模型:{cfg.default_model}")
    typer.echo(f"API Key 环境变量:{cfg.provider.api_key_env}")
    typer.echo("PowerShell 临时设置:")
    typer.echo(f'  $env:{cfg.provider.api_key_env} = "sk-..."')
    typer.echo("检查配置:")
    typer.echo("  mycode doctor")


def _source_lines(result: ConfigLoadResult) -> list[str]:
    if result.files:
        return [str(p) for p in result.files]
    return ["(未找到配置文件,使用内置默认值)"]


def _print_config_context(result: ConfigLoadResult, *, err: bool = False) -> None:
    echo = typer.echo
    echo(f"当前目录:{Path.cwd()}", err=err)
    echo("配置来源:", err=err)
    for source in _source_lines(result):
        echo(f"  {source}", err=err)
    if result.env_overrides:
        echo("环境变量覆盖:" + ", ".join(result.env_overrides), err=err)
    cfg = result.config
    echo(f"provider:{cfg.provider.type}", err=err)
    echo(f"model:{cfg.default_model}", err=err)
    if cfg.provider.profile:
        echo(f"model_profile:{cfg.provider.profile}", err=err)
    echo(f"api_key_env:{cfg.provider.api_key_env}", err=err)
    if cfg.provider.base_url:
        echo(f"base_url:{cfg.provider.base_url}", err=err)


def _print_config_show(result: ConfigLoadResult) -> None:
    cfg = result.config
    key_source = cfg.provider.api_key_source()
    typer.echo(f"default_model:{cfg.default_model}")
    typer.echo(f"max_steps:{cfg.max_steps}")
    typer.echo(f"planning:{cfg.planning}")
    typer.echo(f"planning_max_steps:{cfg.planning_max_steps}")
    typer.echo(f"max_file_lines:{cfg.max_file_lines}")
    typer.echo(f"max_command_output:{cfg.max_command_output}")
    typer.echo(f"context_limit:{cfg.context_limit}")
    typer.echo(f"command_timeout:{cfg.command_timeout}")
    typer.echo("[provider]")
    typer.echo(f"type:{cfg.provider.type}")
    typer.echo(f"profile:{cfg.provider.profile or ''}")
    typer.echo(f"api_key_env:{cfg.provider.api_key_env}")
    typer.echo(f"api_key:{'已设置' if key_source else '未设置'}")
    typer.echo(f"api_key_source:{key_source or ''}")
    typer.echo(f"base_url:{cfg.provider.base_url or ''}")
    typer.echo(f"timeout:{cfg.provider.timeout}")
    typer.echo(f"max_retries:{cfg.provider.max_retries}")
    typer.echo(f"retry_backoff:{cfg.provider.retry_backoff}")
    typer.echo(f"stream_usage:{cfg.provider.stream_usage}")
    if cfg.provider.max_tokens is not None:
        typer.echo(f"max_tokens:{cfg.provider.max_tokens}")
    if cfg.provider.temperature is not None:
        typer.echo(f"temperature:{cfg.provider.temperature}")
    typer.echo("[permissions]")
    typer.echo(f"write:{cfg.permissions.write}")
    typer.echo(f"command:{cfg.permissions.command}")
    typer.echo("[skills]")
    typer.echo(f"active:{', '.join(cfg.skills) or '(无)'}")
    typer.echo("[plugins]")
    typer.echo(f"enabled:{', '.join(cfg.plugins) or '(无)'}")
    typer.echo("[trace]")
    typer.echo(f"enabled:{cfg.trace.enabled}")
    typer.echo(f"directory:{cfg.trace.directory}")
    typer.echo(f"record_prompts:{cfg.trace.record_prompts}")
    typer.echo(f"record_tool_io:{cfg.trace.record_tool_io}")
    typer.echo(f"record_outputs:{cfg.trace.record_outputs}")
    typer.echo(f"otlp_enabled:{cfg.trace.otlp_enabled}")
    if cfg.trace.otlp_endpoint:
        typer.echo(f"otlp_endpoint:{cfg.trace.otlp_endpoint}")
    typer.echo("[codeintel]")
    typer.echo(f"enabled:{cfg.codeintel.enabled}")
    typer.echo(f"auto_context:{cfg.codeintel.auto_context}")
    typer.echo(f"max_context_tokens:{cfg.codeintel.max_context_tokens}")
    typer.echo(f"max_context_fraction:{cfg.codeintel.max_context_fraction}")
    typer.echo(f"max_files:{cfg.codeintel.max_files}")
    typer.echo(f"max_chunks:{cfg.codeintel.max_chunks}")
    typer.echo(f"lsp_timeout:{cfg.codeintel.lsp_timeout}")


def _print_config_where(result: ConfigLoadResult) -> None:
    typer.echo(f"当前目录:{Path.cwd()}")
    typer.echo(f"全局配置:{GLOBAL_CONFIG_PATH}")
    typer.echo(f"项目配置:{LOCAL_CONFIG_PATH}")
    model_store = ModelStore.load()
    typer.echo(f"模型配置档:{model_store.path}")
    typer.echo(f"当前模型配置档:{model_store.active or '(未设置)'}")
    env_config = os.environ.get("MYCODE_CONFIG", "")
    typer.echo(f"MYCODE_CONFIG:{env_config or '(未设置)'}")
    typer.echo("加载顺序:")
    typer.echo("  1. 内置默认值")
    typer.echo(f"  2. 用户全局配置 {GLOBAL_CONFIG_PATH}")
    typer.echo(f"  3. 项目本地配置 {LOCAL_CONFIG_PATH}")
    typer.echo("  4. MYCODE_* 环境变量覆盖")
    typer.echo("实际加载:")
    for source in _source_lines(result):
        typer.echo(f"  {source}")
    if result.env_overrides:
        typer.echo("环境变量覆盖:" + ", ".join(result.env_overrides))


def _handle_config(args: list[str]) -> None:
    sub = args[0] if args else "show"
    try:
        result = load_config_result()
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    if sub == "show":
        _print_config_show(result)
        return
    if sub == "where":
        _print_config_where(result)
        return
    typer.secho("未知 config 命令。可用: mycode config show | mycode config where", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _handle_skill(args: list[str]) -> None:
    sub = args[0] if args else "list"
    if sub == "list":
        active_names: set[str] = set()
        try:
            result = load_config_result()
            active_names = set(result.config.skills)
        except ConfigError:
            pass
        skills = [
            replace(skill, active=skill.name in active_names)
            for skill in discover_skills()
        ]
        typer.echo(format_skill_list(skills))
        return
    typer.secho("未知 skill 命令。可用: mycode skill list", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _handle_plugin(args: list[str]) -> None:
    sub = args[0] if args else "list"
    if sub == "list":
        enabled: set[str] = set()
        try:
            result = load_config_result()
            enabled = set(result.config.plugins)
        except ConfigError:
            pass
        discovered = list_discovered_plugins()
        typer.echo(format_plugin_list(discovered, enabled))
        return
    typer.secho("未知 plugin 命令。可用: mycode plugin list", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _handle_trace(args: list[str]) -> None:
    sub = args[0] if args else "list"
    cfg = load_config_result().config.trace
    trace_dir = cfg.directory

    if sub == "list":
        if not trace_dir.is_dir():
            typer.echo("(暂无 trace)")
            return
        files = sorted(
            [p for p in trace_dir.glob("*.jsonl") if p.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not files:
            typer.echo("(暂无 trace)")
            return
        typer.echo(f"Trace 列表(共 {len(files)} 个,按最近修改排序):")
        for path in files:
            size = path.stat().st_size
            size_str = f"{size} B" if size < 1024 else f"{size / 1024:.1f} KB"
            typer.echo(f"  {path.stem}  {size_str}")
        return

    if sub in {"show", "replay"}:
        if len(args) < 2:
            typer.secho(f"用法: mycode trace {sub} <run_id>", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        run_id = args[1]
        path = trace_dir / f"{run_id}.jsonl"
        if not path.is_file():
            typer.secho(f"找不到 trace:{run_id}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if sub == "show":
            typer.echo(json.dumps([json.loads(line) for line in lines], ensure_ascii=False, indent=2))
        else:
            from mycode.trace import TraceRecord

            records: list[TraceRecord] = []
            for line in lines:
                data = json.loads(line)
                records.append(
                    TraceRecord(
                        schema_version=data.get("schema_version", 1),
                        run_id=data.get("run_id", run_id),
                        seq=data.get("seq", 0),
                        type=data.get("type", ""),
                        timestamp=data.get("timestamp", 0.0),
                        data=data.get("data", {}),
                    )
                )
            for line in replay(records):
                typer.echo(line)
        return

    typer.secho("未知 trace 命令。可用: mycode trace list/show/replay", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _handle_model(args: list[str]) -> None:
    sub = args[0].lower() if args else "list"
    try:
        store = ModelStore.load()
        if sub == "presets":
            typer.echo("可用模型预设:")
            for name in sorted(PRESETS):
                profile = PRESETS[name]
                typer.echo(f"  {name}: {profile.model} [{profile.provider}] {profile.base_url or 'default endpoint'}")
            return

        if sub == "list":
            if not store.profiles:
                typer.echo("(暂无模型配置档。使用 mycode model add <name> --preset deepseek)")
                return
            typer.echo(f"模型配置档: {store.path}")
            for name in sorted(store.profiles):
                profile = store.profiles[name]
                marker = "*" if name == store.active else " "
                source = _profile_key_source(profile)
                typer.echo(f"{marker} {name}: {profile.model} [{profile.provider}] key={source}")
            return

        if sub == "add":
            if len(args) < 2:
                raise ModelStoreError("用法: mycode model add <name> --preset deepseek")
            name = args[1]
            preset_name = _parse_option_value(args[2:], "--preset")
            if preset_name or name.lower() in PRESETS:
                profile = profile_from_preset(preset_name or name, profile_name=name)
            else:
                provider = _parse_option_value(args[2:], "--provider", "openai") or "openai"
                model = _parse_option_value(args[2:], "--model")
                if not model:
                    raise ModelStoreError("自定义配置档必须提供 --model")
                profile = ModelProfile(
                    name=name,
                    provider=provider,
                    model=model,
                    base_url=_parse_option_value(args[2:], "--base-url"),
                    api_key_env=_parse_option_value(args[2:], "--api-key-env"),
                    credential_id=f"profile:{name}",
                )
            profile = _profile_overrides(profile, args[2:])
            store.add(profile, replace=_has_flag(args, "--replace"))
            if not _has_flag(args, "--no-key"):
                secret = typer.prompt("API Key", hide_input=True, confirmation_prompt=True)
                CredentialStore().set(profile.credential_id or f"profile:{name}", secret)
            typer.secho(f"模型配置档已保存: {name}", fg=typer.colors.GREEN)
            if store.active == name:
                typer.echo("该配置档已设为当前模型。")
            return

        if sub == "use":
            if len(args) < 2:
                raise ModelStoreError("用法: mycode model use <name>")
            profile = store.use(args[1])
            typer.secho(f"当前模型已切换为 {profile.name}: {profile.model}", fg=typer.colors.GREEN)
            return

        if sub == "key":
            if len(args) < 2 or args[1] not in store.profiles:
                raise ModelStoreError("用法: mycode model key <name>")
            name = args[1]
            profile = store.profiles[name]
            credential_id = profile.credential_id or f"profile:{name}"
            if profile.credential_id is None:
                profile = replace(profile, credential_id=credential_id)
                store.add(profile, replace=True)
            secret = typer.prompt("新的 API Key", hide_input=True, confirmation_prompt=True)
            CredentialStore().set(credential_id, secret)
            typer.secho(f"{name} 的 API Key 已保存到系统凭据库。", fg=typer.colors.GREEN)
            return

        if sub == "edit":
            if len(args) < 2 or args[1] not in store.profiles:
                raise ModelStoreError("用法: mycode model edit <name> [--model ... --base-url ...]")
            profile = _profile_overrides(store.profiles[args[1]], args[2:])
            store.add(profile, replace=True)
            typer.secho(f"模型配置档已更新: {profile.name}", fg=typer.colors.GREEN)
            return

        if sub == "remove":
            if len(args) < 2:
                raise ModelStoreError("用法: mycode model remove <name> --force")
            if not _has_flag(args, "--force"):
                raise ModelStoreError("确认删除请加 --force")
            profile = store.remove(args[1])
            if profile.credential_id and not _has_flag(args, "--keep-key"):
                CredentialStore().delete(profile.credential_id)
            typer.secho(f"模型配置档已删除: {profile.name}", fg=typer.colors.GREEN)
            return

        raise ModelStoreError("未知 model 命令。可用: presets / list / add / use / key / edit / remove")
    except ModelStoreError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


def _profile_overrides(profile: ModelProfile, args: list[str]) -> ModelProfile:
    updates: dict[str, Any] = {}
    options = {
        "provider": "--provider",
        "model": "--model",
        "base_url": "--base-url",
        "api_key_env": "--api-key-env",
        "max_tokens": "--max-tokens",
        "temperature": "--temperature",
        "top_p": "--top-p",
        "thinking": "--thinking",
        "thinking_format": "--thinking-format",
        "thinking_budget": "--thinking-budget",
        "reasoning_effort": "--reasoning-effort",
    }
    parsers: dict[str, Callable[[str], Any]] = {
        "max_tokens": int,
        "temperature": float,
        "top_p": float,
        "thinking_budget": int,
    }
    for field_name, option in options.items():
        value = _parse_option_value(args, option)
        if value is not None:
            try:
                updates[field_name] = parsers[field_name](value)
            except KeyError:
                updates[field_name] = value
            except ValueError as exc:
                raise ModelStoreError(f"{option} 的值无效: {value}") from exc
    return replace(profile, **updates) if updates else profile


def _profile_key_source(profile: ModelProfile) -> str:
    if profile.api_key_env and os.environ.get(profile.api_key_env, "").strip():
        return f"env:{profile.api_key_env}"
    if profile.credential_id:
        try:
            if CredentialStore().get(profile.credential_id):
                return "system-keyring"
        except ModelStoreError:
            return "keyring-unavailable"
    return "missing"


def _handle_doctor(args: list[str] | None = None) -> None:
    args = [] if args is None else args
    check_api = _has_flag(args, "--api")
    try:
        result = load_config_result()
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    _print_config_context(result)
    cfg = result.config
    from mycode.codeintel.lsp import discover_server

    typer.echo("代码智能:")
    typer.echo(f"  enabled: {cfg.codeintel.enabled}")
    typer.echo(f"  auto_context: {cfg.codeintel.auto_context}")
    for language in ("python", "typescript"):
        command = discover_server(Path.cwd(), language, cfg.codeintel.language_servers.get(language))
        if command:
            typer.secho(f"  {language} LSP: {' '.join(command)}", fg=typer.colors.GREEN)
        else:
            typer.secho(f"  {language} LSP: 未找到，将使用本地降级", fg=typer.colors.YELLOW)
    sessions = Session.list_all()
    corrupt_count = Session.corrupt_count()
    typer.echo(f"会话文件: {len(sessions)} 个")
    if corrupt_count:
        typer.secho(f"损坏会话: {corrupt_count} 个", fg=typer.colors.YELLOW)
    key_source = cfg.provider.api_key_source()
    if key_source:
        key = cfg.provider.resolve_api_key()
        source_label = cfg.provider.api_key_env if key_source == "environment" else "系统凭据库"
        typer.secho(f"API Key:已设置({source_label})", fg=typer.colors.GREEN)
        if check_api:
            try:
                provider = build_provider(cfg, key)
                provider.chat([{"role": "user", "content": "ping"}], tools=None)
            except Exception as exc:  # noqa: BLE001 - doctor should explain provider failures.
                typer.secho(f"API 连通性:失败:{type(exc).__name__}: {exc}", fg=typer.colors.RED)
                raise typer.Exit(code=1) from exc
            typer.secho("API 连通性:成功", fg=typer.colors.GREEN)
        return

    typer.secho("API Key:未设置", fg=typer.colors.RED)
    if cfg.provider.profile:
        typer.echo(f"推荐长期保存: mycode model key {cfg.provider.profile}")
    if cfg.provider.api_key_env:
        typer.echo("CMD 临时设置:")
        typer.echo(f'  set "{cfg.provider.api_key_env}=sk-..."')
        typer.echo("PowerShell 临时设置:")
        typer.echo(f'  $env:{cfg.provider.api_key_env} = "sk-..."')
    raise typer.Exit(code=1)


def _handle_index(args: list[str]) -> None:
    from mycode.codeintel.index import SymbolIndex

    sub = args[0] if args else "status"
    index = SymbolIndex(Path.cwd())
    if sub == "build":
        result = index.build()
        typer.echo(
            f"索引完成:发现 {result.discovered}，更新 {result.indexed}，"
            f"未变 {result.unchanged}，删除 {result.removed}"
        )
        for error in result.errors[:20]:
            typer.secho(f"  {error}", fg=typer.colors.YELLOW, err=True)
        return
    if sub == "status":
        status = index.status()
        for name in ("path", "files", "symbols", "dependencies"):
            typer.echo(f"{name}:{status[name]}")
        return
    if sub == "clear":
        if "--force" not in args:
            typer.echo("确认清除本地代码索引请加 --force。")
            raise typer.Exit(code=1)
        index.clear()
        typer.echo("代码索引已清除。")
        return
    typer.secho("未知 index 命令。可用: build / status / clear", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _handle_session(args: list[str]) -> None:
    sub = args[0] if args else ""
    if sub == "show":
        if len(args) < 2:
            typer.secho("用法: mycode session show <id>", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        try:
            session = Session.load(args[1])
        except SessionError as exc:
            typer.secho(f"会话文件损坏:{exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
        if session is None:
            typer.secho(f"找不到会话:{args[1]}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        typer.echo(f"id:{session.id}")
        typer.echo(f"model:{session.model}")
        typer.echo(f"provider:{session.provider}")
        typer.echo(f"created_at:{session.created_at}")
        typer.echo(f"updated_at:{session.updated_at}")
        typer.echo(f"turns:{session.turn_count()}")
        preview = session.first_user_text().replace("\n", " ")
        typer.echo(f"first_user:{preview[:120]}")
        return

    if sub == "export":
        if len(args) < 2:
            typer.secho("用法: mycode session export <id> --format markdown|json --output <path>", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        try:
            session = Session.load(args[1])
        except SessionError as exc:
            typer.secho(f"会话文件损坏:{exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
        if session is None:
            typer.secho(f"找不到会话:{args[1]}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        fmt = (_parse_option_value(args[2:], "--format", "json") or "json").lower()
        output = _parse_option_value(args[2:], "--output")
        if not output:
            typer.secho("session export 需要 --output <path>", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        target = Path(output)
        if fmt == "json":
            content = json.dumps(_session_payload(session), ensure_ascii=False, indent=2)
        elif fmt in {"markdown", "md"}:
            content = _render_session_markdown(session)
        else:
            typer.secho("session export 的 --format 只能是 json 或 markdown", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        typer.secho(f"已导出会话:{target}", fg=typer.colors.GREEN)
        return

    if sub == "delete":
        if len(args) < 2:
            typer.secho("用法: mycode session delete <id>", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        try:
            session = Session.load(args[1])
        except SessionError as exc:
            typer.secho(f"会话文件损坏:{exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
        if session is None:
            typer.secho(f"找不到会话:{args[1]}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        if not typer.confirm(f"确认删除会话 {session.id}?", default=False):
            typer.echo("已取消。")
            return
        session.path.unlink()
        typer.secho(f"已删除会话:{session.id}", fg=typer.colors.GREEN)
        return

    if sub == "prune":
        keep_raw = _parse_option_value(args[1:], "--keep", "20")
        try:
            keep = int(keep_raw or "20")
        except ValueError as exc:
            typer.secho("--keep 必须是整数", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
        if keep < 0:
            typer.secho("--keep 不能小于 0", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        sessions = Session.list_all()
        doomed = sessions[keep:]
        if not doomed:
            typer.echo(f"无需清理。当前 {len(sessions)} 个会话,保留 {keep} 个。")
            return
        if not typer.confirm(f"确认删除 {len(doomed)} 个旧会话?", default=False):
            typer.echo("已取消。")
            return
        for session in doomed:
            session.path.unlink(missing_ok=True)
        typer.secho(f"已删除 {len(doomed)} 个旧会话。", fg=typer.colors.GREEN)
        return

    typer.secho(
        "未知 session 命令。可用: show/export/delete/prune",
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(code=1)


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def main(
    ctx: typer.Context,
    task: str = typer.Argument(
        None,
        metavar="TASK",
        help='要执行的任务;省略进入交互模式;传 "tui" / "web" / "serve" / "sessions" / "init" / "doctor" / "config" / "session" / "eval" 执行内置命令',
    ),
    resume: str = typer.Option(None, "--resume", help="恢复指定 id 的会话"),
    continue_: bool = typer.Option(False, "--continue", help="接最近一次会话"),
    sessions_flag: bool = typer.Option(False, "--sessions", help="列出所有会话并退出"),
    undo: bool = typer.Option(False, "--undo", help="撤销最近一次 mycode 任务写入"),
    commit: bool = typer.Option(False, "--commit", help="将本轮 mycode 改过的文件提交到 git"),
    budget: float | None = typer.Option(None, "--budget", help="本次调用美元预算,超出后在轮间停止"),
    trace: bool = typer.Option(False, "--trace", help="本次运行启用 trace 记录"),
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="显示版本并退出。",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """mycode - 一个极简的终端编码 agent。"""
    setup_logging()
    if sessions_flag or task == "sessions":
        _print_sessions()
        return
    if task == "init":
        _handle_init(list(ctx.args))
        return
    if task == "doctor":
        _handle_doctor(list(ctx.args))
        return
    if task == "model":
        _handle_model(list(ctx.args))
        return
    if task == "config":
        _handle_config(list(ctx.args))
        return
    if task == "session":
        _handle_session(list(ctx.args))
        return
    if task == "skill":
        _handle_skill(list(ctx.args))
        return
    if task == "plugin":
        _handle_plugin(list(ctx.args))
        return
    if task == "trace":
        _handle_trace(list(ctx.args))
        return
    if task == "eval":
        from mycode.evals.cli import handle_eval

        handle_eval(list(ctx.args))
        return
    if task == "index":
        _handle_index(list(ctx.args))
        return
    if task == "tui":
        try:
            from mycode.tui import run_tui
        except ImportError as exc:
            typer.secho(
                "TUI 需要额外依赖。请运行: pip install 'mycode-ai-cli[tui]'",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1) from exc
        run_tui(session_id=resume)
        return
    if task == "web":
        try:
            from mycode.web import run_web_server
            from mycode.web.server import WebRuntimeError
        except ImportError as exc:
            typer.secho(
                "Web 工作台需要额外依赖。请运行: pip install 'mycode-ai-cli[web]'",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1) from exc
        port_text = _parse_option_value(list(ctx.args), "--port", "0") or "0"
        try:
            port = int(port_text)
            run_web_server(port=port, open_browser=not _has_flag(list(ctx.args), "--no-open"))
        except (ValueError, WebRuntimeError) as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
        return
    if task == "serve":
        if "--stdio" not in ctx.args:
            typer.secho("serve 当前仅支持 --stdio", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)
        from mycode.server import run_stdio_server

        run_stdio_server()
        return
    if undo:
        _handle_undo(resume)
        return

    try:
        config_result = load_config_result()
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    if trace:
        config_result = replace(
            config_result,
            config=config_with_trace_overrides(config_result.config, enabled=True),
        )

    try:
        runtime = MyCodeRuntime.from_config_result(config_result, project_root=Path.cwd())
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        _print_config_context(config_result, err=True)
        raise typer.Exit(code=1) from exc

    if runtime.info.plugin_errors:
        for err in runtime.info.plugin_errors:
            typer.secho(err, fg=typer.colors.YELLOW, err=True)
    if runtime.info.missing_skills:
        typer.secho(
            f"未找到以下 Skill:{', '.join(runtime.info.missing_skills)}",
            fg=typer.colors.YELLOW,
            err=True,
        )

    if resume:
        try:
            session = runtime.get_session(resume)
        except LookupError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
        typer.secho(f"已恢复会话 {session.id}({session.turn_count()} 轮)", fg=typer.colors.GREEN)
    elif continue_:
        session = Session.latest()
        if session is None:
            typer.secho("没有可继续的会话。", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        typer.secho(f"已继续最近会话 {session.id}({session.turn_count()} 轮)", fg=typer.colors.GREEN)
    else:
        session = runtime.new_session()

    typer.echo(f"模型:{runtime.config.default_model}")
    typer.secho(f"会话 {session.id}(可用 mycode --resume {session.id} 恢复)", fg=typer.colors.BRIGHT_BLACK)
    typer.secho(SECURITY_NOTICE, fg=typer.colors.BRIGHT_BLACK)
    commit_state: GitState | None = None
    if commit:
        commit_state = prepare_auto_commit()
        if not commit_state.enabled and commit_state.reason:
            typer.secho(commit_state.reason, fg=typer.colors.YELLOW)

    if task is not None:
        typer.secho(f"任务:{task}", fg=typer.colors.CYAN, bold=True)
        result = runtime.run_prompt(session, task, budget_usd=budget)
        if result.trace_path:
            typer.secho(f"Trace 已保存:{result.trace_path}", fg=typer.colors.BRIGHT_BLACK)
        if commit_state is not None and commit_state.enabled:
            checkpoint = Checkpoint.latest(session.id)
            if checkpoint is not None:
                typer.echo(auto_commit_checkpoint(runtime.provider, checkpoint, state=commit_state))
        return

    _run_repl(runtime, session, config_result, commit_state, budget)


def _run_repl(
    runtime: MyCodeRuntime,
    session: Session,
    config_result: ConfigLoadResult,
    commit_state: GitState | None = None,
    budget: float | None = None,
) -> None:
    """交互式多轮对话:沿用同一个 messages,跨轮保留上下文,并即时存档。"""
    cfg = config_result.config
    typer.secho(
        "进入交互模式:多轮对话会保留上下文;输入 /help 查看命令,/exit 退出。",
        fg=typer.colors.GREEN,
    )
    typer.echo(f"当前目录:{Path.cwd()}")
    typer.echo(f"模型:{cfg.default_model}")
    typer.echo(f"provider:{cfg.provider.type}")
    typer.echo("配置来源:" + ", ".join(_source_lines(config_result)))
    commands = load_commands(builtins={item[1:] for item in _BUILTIN_SLASH})
    while True:
        try:
            line = input("\nmycode> ").strip()
        except (EOFError, KeyboardInterrupt):
            typer.echo("\n再见。")
            return

        if not line:
            typer.echo("请输入任务,或输入 /help 查看可用命令。首次使用可先运行 mycode init 和 mycode doctor。")
            continue
        lower = line.lower()
        if lower in _EXIT_WORDS or lower == "/exit":
            typer.echo("再见。")
            return
        if lower == "/help":
            _print_repl_help(commands)
            continue
        if lower == "/sessions":
            _print_sessions()
            continue
        if lower == "/doctor":
            try:
                _handle_doctor([])
            except typer.Exit:
                pass
            continue
        if lower == "/model":
            typer.echo(f"model:{cfg.default_model}")
            typer.echo(f"provider:{cfg.provider.type}")
            typer.echo(f"profile:{cfg.provider.profile or ''}")
            typer.echo(f"api_key_env:{cfg.provider.api_key_env}")
            typer.echo(f"api_key_source:{cfg.provider.api_key_source() or ''}")
            continue
        if lower == "/undo":
            _handle_undo(session.id)
            continue
        if lower == "/diff":
            checkpoint = Checkpoint.latest(session.id)
            result = render_diff_for_checkpoint(checkpoint)
            if result.startswith("错误:"):
                typer.secho(result, fg=typer.colors.RED)
            elif result.startswith("("):
                typer.echo(result)
            continue
        if line.startswith("/"):
            raw = line[1:]
            name, _, args = raw.partition(" ")
            command = commands.get(name)
            if command is None or command.shadowed:
                typer.echo("未知斜杠命令。输入 /help 查看可用命令。")
                continue
            line = command.render(args.strip())

        try:
            result = runtime.run_prompt(session, line, budget_usd=budget)
        except KeyboardInterrupt:
            typer.echo("\n已中断。会话已存档。")
            return
        if result.trace_path:
            typer.secho(f"Trace 已保存:{result.trace_path}", fg=typer.colors.BRIGHT_BLACK)
        checkpoint = Checkpoint.latest(session.id)
        if commit_state is not None and commit_state.enabled and checkpoint is not None:
            typer.echo(auto_commit_checkpoint(runtime.provider, checkpoint, state=commit_state))


if __name__ == "__main__":
    app()
