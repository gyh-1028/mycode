"""Custom REPL slash commands loaded from markdown templates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mycode.config import GLOBAL_CONFIG_PATH

LOCAL_COMMANDS_DIR = Path(".mycode") / "commands"
GLOBAL_COMMANDS_DIR = GLOBAL_CONFIG_PATH.parent / "commands"


@dataclass(frozen=True)
class SlashCommand:
    name: str
    template: str
    description: str = ""
    path: Path | None = None
    shadowed: bool = False

    def render(self, args: str) -> str:
        return self.template.replace("$ARGS", args)


def _parse_command_file(path: Path, *, shadowed: bool = False) -> SlashCommand | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    description = ""
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            frontmatter = text[4:end].strip()
            body = text[end + 4 :].lstrip("\r\n")
            for line in frontmatter.splitlines():
                if line.lower().startswith("description:"):
                    description = line.split(":", 1)[1].strip().strip("'\"")
                    break
    return SlashCommand(
        name=path.stem,
        template=body.strip(),
        description=description,
        path=path,
        shadowed=shadowed,
    )


def _load_dir(directory: Path, *, builtins: set[str]) -> dict[str, SlashCommand]:
    out: dict[str, SlashCommand] = {}
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.md")):
        cmd = _parse_command_file(path, shadowed=path.stem in builtins)
        if cmd is not None:
            out[cmd.name] = cmd
    return out


def load_commands(*, builtins: set[str]) -> dict[str, SlashCommand]:
    commands = _load_dir(GLOBAL_COMMANDS_DIR, builtins=builtins)
    commands.update(_load_dir(LOCAL_COMMANDS_DIR, builtins=builtins))
    return commands
