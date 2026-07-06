import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { FileTree } from "./FileTree";

describe("FileTree", () => {
  it("loads directories lazily and opens files", async () => {
    const load = vi.fn().mockResolvedValue([{ name: "app.py", path: "src/app.py", type: "file", language: "python" }]);
    const open = vi.fn();
    render(<FileTree entries={[{ name: "src", path: "src", type: "directory" }]} load={load} open={open} />);
    fireEvent.click(screen.getByTitle("src"));
    await waitFor(() => expect(load).toHaveBeenCalledWith("src"));
    fireEvent.click(screen.getByTitle("src/app.py"));
    expect(open).toHaveBeenCalledWith("src/app.py");
  });
});
