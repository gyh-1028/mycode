"""Git tools exposed to the agent.

All functions are read-only: they inspect repository state but never modify it.
"""

from mycode.git_ops import git_branch, git_diff, git_log, git_status
from mycode.tools.registry import register


@register(
    name="git_status",
    description="查看当前 git 仓库的工作区状态(哪些文件被修改、新增、删除)。",
    parameters={
        "type": "object",
        "properties": {
            "short": {
                "type": "boolean",
                "default": True,
                "description": "是否返回简短状态(默认 true)。",
            }
        },
    },
)
def _git_status(short: bool = True) -> str:
    return git_status(short=short)


@register(
    name="git_log",
    description="查看最近 n 条 git 提交日志(单行格式,含分支信息)。",
    parameters={
        "type": "object",
        "properties": {
            "n": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 10,
                "description": "返回的提交条数,默认 10。",
            }
        },
    },
)
def _git_log(n: int = 10) -> str:
    return git_log(n=n)


@register(
    name="git_branch",
    description="查看当前分支以及本地分支列表。",
    parameters={"type": "object", "properties": {}},
)
def _git_branch() -> str:
    return git_branch()


@register(
    name="git_diff",
    description="查看当前 Git 工作区或暂存区的 tracked file unified diff，只读且不会修改仓库。",
    parameters={
        "type": "object",
        "properties": {
            "staged": {
                "type": "boolean",
                "default": False,
                "description": "true 查看暂存区，false 查看未暂存改动。",
            }
        },
    },
)
def _git_diff(staged: bool = False) -> str:
    return git_diff(staged=staged)
