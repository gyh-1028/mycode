"""Canonical eval cases for mycode.

Each case runs fully offline: a FakeProvider supplies scripted model responses,
real tools execute against a temp workspace, and structured scorers verify the
outcome. These cases are the P6 regression gate — P7-P10 features must add
their own cases here.
"""

from mycode.evals.types import EvalCase
from mycode.llm.base import LLMResponse, StopReason, ToolCall

CASES: list[EvalCase] = []


# --------------------------------------------------------------------------- #
# 1. read_then_answer — basic read-modify (agent reads a file and reports it)
# --------------------------------------------------------------------------- #
CASES.append(
    EvalCase(
        name="read_then_answer",
        description="读取文件并汇报内容",
        prompt="读取 notes.txt 的内容并告诉我",
        files={"notes.txt": "hello world\n"},
        responses=[
            LLMResponse(
                tool_calls=[ToolCall(id="c1", name="read_file", args={"path": "notes.txt"})],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            LLMResponse(
                text="完成内容: notes.txt 的内容是 hello world\n验证命令/结果: 无需验证\n未完成事项: 无",
                stop_reason=StopReason.END_TURN,
            ),
        ],
        expected_contains=["hello world"],
        expected_tool_sequence=["read_file"],
        expected_status="completed",
    )
)


# --------------------------------------------------------------------------- #
# 2. edit_file_fix — read then edit a file (file-state scorer)
# --------------------------------------------------------------------------- #
CASES.append(
    EvalCase(
        name="edit_file_fix",
        description="读取并修复 bug.py 中的 add 函数",
        prompt="修复 bug.py 中的 add 函数,它应该返回 a + b 而不是 a - b",
        files={"bug.py": "def add(a, b):\n    return a - b\n"},
        responses=[
            LLMResponse(
                tool_calls=[ToolCall(id="c1", name="read_file", args={"path": "bug.py"})],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            LLMResponse(
                tool_calls=[
                    ToolCall(
                        id="c2",
                        name="edit_file",
                        args={"path": "bug.py", "old_str": "return a - b", "new_str": "return a + b"},
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            LLMResponse(
                text="完成内容: 已修复 add 函数\n验证命令/结果: 无\n未完成事项: 无",
                stop_reason=StopReason.END_TURN,
            ),
        ],
        expected_tool_sequence=["read_file", "edit_file"],
        expected_files={"bug.py": "def add(a, b):\n    return a + b\n"},
        expected_status="completed",
    )
)


# --------------------------------------------------------------------------- #
# 3. shell_fail_then_fix — run a failing test, edit, rerun (passes)
# --------------------------------------------------------------------------- #
CASES.append(
    EvalCase(
        name="shell_fail_then_fix",
        description="运行失败的测试,修复后重跑通过",
        prompt="运行测试 test_calc.py,如果失败就修复 calc.py",
        files={
            "calc.py": "def add(a, b):\n    return a - b\n",
            "test_calc.py": "from calc import add\nassert add(1, 1) == 2\nprint('pass')\n",
        },
        responses=[
            LLMResponse(
                tool_calls=[ToolCall(id="c1", name="run_bash", args={"command": "python test_calc.py"})],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            LLMResponse(
                tool_calls=[
                    ToolCall(
                        id="c2",
                        name="edit_file",
                        args={"path": "calc.py", "old_str": "return a - b", "new_str": "return a + b"},
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            LLMResponse(
                tool_calls=[ToolCall(id="c3", name="run_bash", args={"command": "python test_calc.py"})],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            LLMResponse(
                text="完成内容: 修复了 calc.py 的 add 函数\n验证命令/结果: python test_calc.py 通过\n未完成事项: 无",
                stop_reason=StopReason.END_TURN,
            ),
        ],
        expected_tool_sequence=["run_bash", "edit_file", "run_bash"],
        expected_files={"calc.py": "def add(a, b):\n    return a + b\n"},
        expected_status="completed",
    )
)


# --------------------------------------------------------------------------- #
# 4. permission_denied — agent tries to read a sensitive .env file
# --------------------------------------------------------------------------- #
CASES.append(
    EvalCase(
        name="permission_denied",
        description="尝试读取 .env 被权限拒绝",
        prompt="读取 .env 文件的内容",
        files={".env": "SECRET=super-secret-value\n"},
        responses=[
            LLMResponse(
                tool_calls=[ToolCall(id="c1", name="read_file", args={"path": ".env"})],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            LLMResponse(
                text="完成内容: 无法读取 .env,它是敏感文件\n验证命令/结果: 无\n未完成事项: 无",
                stop_reason=StopReason.END_TURN,
            ),
        ],
        expected_permission_denied=True,
        expected_tool_sequence=["read_file"],
        expected_status="completed",
    )
)


# --------------------------------------------------------------------------- #
# 5. loop_detection — repeated identical error triggers stuck detection
# --------------------------------------------------------------------------- #
CASES.append(
    EvalCase(
        name="loop_detection",
        description="同一报错连续出现触发卡住检测",
        prompt="修复失败的测试",
        files={"broken.py": "x = 1\n"},
        responses=[
            LLMResponse(
                tool_calls=[ToolCall(id="c1", name="run_bash", args={"command": "python -c \"assert False\""})],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            LLMResponse(
                tool_calls=[ToolCall(id="c2", name="run_bash", args={"command": "python -c \"assert False\""})],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            LLMResponse(
                tool_calls=[ToolCall(id="c3", name="run_bash", args={"command": "python -c \"assert False\""})],
                stop_reason=StopReason.TOOL_CALLS,
            ),
        ],
        expected_status="stuck",
        # protected_files: broken.py must not be modified (agent only ran bash)
        protected_files=["broken.py"],
    )
)


# --------------------------------------------------------------------------- #
# 6. session_resume — continue from pre-existing messages
# --------------------------------------------------------------------------- #
CASES.append(
    EvalCase(
        name="session_resume",
        description="从已有会话上下文继续回答",
        prompt="刚才读取的文件内容是什么?",
        files={"data.txt": "the answer is 42\n"},
        initial_messages=[
            {"role": "system", "content": "你是 mycode。"},
            {"role": "user", "content": "读取 data.txt"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "prev_c1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "data.txt"}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "prev_c1", "content": "the answer is 42"},
        ],
        responses=[
            LLMResponse(
                text="完成内容: 刚才读取的 data.txt 内容是 the answer is 42\n验证命令/结果: 无\n未完成事项: 无",
                stop_reason=StopReason.END_TURN,
            ),
        ],
        expected_contains=["42"],
        expected_tool_sequence=[],  # no new tool calls in this turn
        expected_status="completed",
    )
)
