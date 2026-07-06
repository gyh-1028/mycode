from mycode.approvals import (
    ApprovalRequest,
    approval_handler,
    effective_permission,
    permission_scope,
)
from mycode.ui import request_command_approval, request_write_approval


def test_injected_write_approval_receives_diff() -> None:
    seen: list[ApprovalRequest] = []
    with approval_handler(lambda request: seen.append(request) or True):
        assert request_write_approval("a.py", "--- a/a.py\n+++ b/a.py", mode="ask")
    assert seen[0].kind == "write"
    assert seen[0].display_path == "a.py"
    assert seen[0].diff.startswith("--- a/")


def test_injected_command_approval_can_deny() -> None:
    seen: list[ApprovalRequest] = []
    with approval_handler(lambda request: seen.append(request) or False):
        assert not request_command_approval("pytest", mode="ask", risk="read")
    assert seen[0].kind == "command"
    assert seen[0].command == "pytest"
    assert seen[0].risk == "read"


def test_permission_scope_overrides_configured_policy_and_resets() -> None:
    assert effective_permission("ask") == "ask"
    with permission_scope("read-only"):
        assert effective_permission("allow") == "deny"
    with permission_scope("full-access"):
        assert effective_permission("ask") == "allow"
        assert effective_permission("deny") == "allow"
    assert effective_permission("ask") == "ask"
