"""Skill support: reusable workflow instructions stored in SKILL.md files.

A Skill is a directory under ``.mycode/skills/<name>/`` containing ``SKILL.md``.
The file may include YAML-ish frontmatter (key: value) followed by Markdown
instructions. Only skills explicitly listed in config ``skills`` or the
``MYCODE_SKILLS`` env variable are activated.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

LOCAL_SKILLS_DIR = Path(".mycode") / "skills"
GLOBAL_SKILLS_DIR = Path.home() / ".mycode" / "skills"


@dataclass(frozen=True)
class SkillManifest:
    """Metadata and content of a discovered skill."""

    name: str
    version: str
    description: str
    required_tools: list[str]
    content: str
    path: Path | None = None
    active: bool = False


def _parse_skill_file(path: Path) -> SkillManifest | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    name = path.parent.name
    version = "0.0.1"
    description = ""
    required_tools: list[str] = []
    content = text.strip()

    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            frontmatter = text[4:end].strip()
            content = text[end + 4 :].lstrip("\r\n").strip()
            for line in frontmatter.splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                key = key.strip().lower()
                value = value.strip().strip("'\"")
                if key == "name":
                    name = value or name
                elif key == "version":
                    version = value or version
                elif key == "description":
                    description = value
                elif key in {"required_tools", "required-tools"}:
                    required_tools = [
                        item.strip() for item in value.split(",") if item.strip()
                    ]

    return SkillManifest(
        name=name,
        version=version,
        description=description,
        required_tools=required_tools,
        content=content,
        path=path,
    )


def discover_skills() -> list[SkillManifest]:
    """Discover all available skills, with local skills overriding global ones."""
    found: dict[str, SkillManifest] = {}
    for base in (GLOBAL_SKILLS_DIR, LOCAL_SKILLS_DIR):
        if not base.is_dir():
            continue
        for skill_dir in sorted(base.iterdir()):
            if not skill_dir.is_dir():
                continue
            md = skill_dir / "SKILL.md"
            if not md.is_file():
                continue
            skill = _parse_skill_file(md)
            if skill is not None:
                found[skill.name] = skill
    return list(found.values())


def load_active_skills(
    active_names: Iterable[str],
) -> tuple[list[SkillManifest], list[str]]:
    """Return (active skills, missing names)."""
    available = {s.name: s for s in discover_skills()}
    active: list[SkillManifest] = []
    missing: list[str] = []
    for name in active_names:
        if name in available:
            active.append(replace(available[name], active=True))
        else:
            missing.append(name)
    return active, missing


def format_skill_list(skills: list[SkillManifest]) -> str:
    lines = [f"发现 {len(skills)} 个 Skill:"]
    for s in skills:
        marker = "[已激活]" if s.active else "[未激活]"
        lines.append(f"  {marker} {s.name} v{s.version} - {s.description or '(无描述)'}")
    return "\n".join(lines)
