import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RunInspector } from "./RunInspector";

afterEach(cleanup);

const baseProps = {
  tab: "activity" as const,
  onTabChange: vi.fn(),
  onClose: vi.fn(),
  activities: [],
  diff: { checkpointId: null, files: [], diff: "" },
  compacting: false,
  undoing: false,
  runActive: false,
  onCompact: vi.fn(),
  onUndo: vi.fn(),
  onOpenFile: vi.fn(),
};

describe("RunInspector", () => {
  it("shows expandable tool details without discarding arguments", () => {
    render(<RunInspector {...baseProps} activities={[{
      id: "tool-1",
      kind: "tool",
      name: "read_file",
      summary: "返回 20 字符",
      detail: '{"path":"src/app.py"}',
      status: "done",
      duration: 4,
    }]} />);

    fireEvent.click(screen.getByText("read_file"));
    expect(screen.getByText('{"path":"src/app.py"}')).toBeInTheDocument();
    expect(screen.getByText("4ms")).toBeInTheDocument();
  });

  it("renders per-file diff, undo, and context navigation", () => {
    const onUndo = vi.fn();
    const onOpenFile = vi.fn();
    const { rerender } = render(<RunInspector
      {...baseProps}
      tab="diff"
      diff={{ checkpointId: "cp-1", diff: "", files: [{ path: "src/app.py", kind: "modified", diff: "@@ -1 +1 @@\n-old\n+new" }] }}
      onUndo={onUndo}
      onOpenFile={onOpenFile}
    />);
    fireEvent.click(screen.getByRole("button", { name: "打开文件" }));
    expect(onOpenFile).toHaveBeenCalledWith("src/app.py");
    fireEvent.click(screen.getByRole("button", { name: "撤销本轮" }));
    fireEvent.click(screen.getByRole("button", { name: "确认" }));
    expect(onUndo).toHaveBeenCalledOnce();

    rerender(<RunInspector
      {...baseProps}
      tab="context"
      context={{ estimated_tokens: 42, paths: ["src/app.py"], degraded: [], items: [{ path: "src/app.py", symbol: "greet", reason: "符号匹配", score: 8, start_line: 1, end_line: 3 }] }}
      onOpenFile={onOpenFile}
    />);
    fireEvent.click(screen.getByText("greet"));
    expect(onOpenFile).toHaveBeenCalledWith("src/app.py", 1);
  });
});
