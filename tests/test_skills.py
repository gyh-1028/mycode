"""Tests for Skill discovery and prompt injection."""

from mycode.prompts import build_system_prompt
from mycode.skills import SkillManifest, discover_skills, load_active_skills


def test_discover_skills_parses_frontmatter(tmp_path, monkeypatch) -> None:
    skills_dir = tmp_path / ".mycode" / "skills"
    skill_dir = skills_dir / "review"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(
        "---\nname: code-review\nversion: 1.0.0\ndescription: Review code changes\nrequired_tools: git_status, git_diff\n---\n"
        "Always review diffs before editing.\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    skills = discover_skills()
    assert len(skills) == 1
    skill = skills[0]
    assert skill.name == "code-review"
    assert skill.version == "1.0.0"
    assert skill.description == "Review code changes"
    assert skill.required_tools == ["git_status", "git_diff"]
    assert "Always review diffs" in skill.content


def test_load_active_skills_returns_missing_names(tmp_path, monkeypatch) -> None:
    skills_dir = tmp_path / ".mycode" / "skills" / "s1"
    skills_dir.mkdir(parents=True)
    skills_dir.joinpath("SKILL.md").write_text("Do X.\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    active, missing = load_active_skills(["s1", "missing"])
    assert [s.name for s in active] == ["s1"]
    assert active[0].active is True
    assert missing == ["missing"]


def test_build_system_prompt_includes_active_skills() -> None:
    skill = SkillManifest(
        name="test",
        version="0.1.0",
        description="",
        required_tools=[],
        content="Always run tests.",
        active=True,
    )
    prompt = build_system_prompt(active_skills=[skill])
    assert "已激活的 Skill" in prompt
    assert "Always run tests" in prompt


def test_build_system_prompt_without_skills_unchanged(tmp_path) -> None:
    prompt = build_system_prompt(root=tmp_path)
    assert "已激活的 Skill" not in prompt
