import { describe, expect, it } from "vitest";
import { AgentEvent } from "./protocol";
import { initialRunState, runReducer } from "./state";

function event(seq: number, type: string, payload: Record<string, unknown>): AgentEvent {
  return { schema_version: 1, run_id: "run-1", seq, type, timestamp: seq, payload };
}

describe("runReducer", () => {
  it("projects streaming text and usage", () => {
    let state = runReducer(initialRunState, { type: "user", content: "修复测试" });
    state = runReducer(state, { type: "event", event: event(1, "model.stream.text", { content: "完成" }) });
    state = runReducer(state, { type: "event", event: event(2, "model.stream.end", {}) });
    state = runReducer(state, { type: "event", event: event(3, "usage.reported", { prompt_tokens: 10, completion_tokens: 3, cached_tokens: 2, estimated_cost: 0.01 }) });
    expect(state.messages.at(-1)).toEqual({ role: "assistant", content: "完成" });
    expect(state.stream).toBe("");
    expect(state.usage).toEqual({ input: 10, output: 3, cached: 2, cost: 0.01 });
  });

  it("pairs tool completion with the latest running activity", () => {
    let state = runReducer(initialRunState, { type: "event", event: event(1, "tool.call.started", { name: "read_file", tool_call_id: "t1", args_preview: "app.py" }) });
    state = runReducer(state, { type: "event", event: event(2, "tool.call.finished", { name: "read_file", tool_call_id: "t1", result_len: 20, duration_ms: 4, is_error: false }) });
    expect(state.activities).toEqual([{ id: "t1", name: "read_file", detail: "20 字符", duration: 4, status: "done" }]);
  });

  it("records selected context without adding it to messages", () => {
    const state = runReducer(initialRunState, { type: "event", event: event(1, "context.selected", { paths: ["app.py"], tokens: 20 }) });
    expect(state.context).toContain("app.py");
    expect(state.messages).toHaveLength(0);
  });

  it("records context capacity from context.capacity events", () => {
    let state = runReducer(initialRunState, { type: "event", event: event(1, "context.capacity", { used_tokens: 1200, limit: 4000, percent: 30 }) });
    expect(state.capacity).toEqual({ used: 1200, limit: 4000, percent: 30 });
    state = runReducer(state, { type: "event", event: event(2, "context.capacity", { used_tokens: 3200, limit: 4000, percent: 80 }) });
    expect(state.capacity).toEqual({ used: 3200, limit: 4000, percent: 80 });
  });

  it("records context.compacted and manual compacted action", () => {
    let state = runReducer(initialRunState, { type: "event", event: event(1, "context.compacted", {}) });
    expect(state.compacted).toBe(true);
    state = runReducer(state, { type: "load", messages: [] });
    expect(state.compacted).toBeUndefined();
    state = runReducer(state, { type: "compacted", compacted: true });
    expect(state.compacted).toBe(true);
  });

  it("clears capacity and compacted on run.started", () => {
    let state = runReducer(initialRunState, { type: "event", event: event(1, "context.capacity", { used_tokens: 1, limit: 10, percent: 10 }) });
    state = runReducer(state, { type: "event", event: event(2, "context.compacted", {}) });
    state = runReducer(state, { type: "event", event: event(3, "run.started", {}) });
    expect(state.capacity).toBeUndefined();
    expect(state.compacted).toBe(false);
  });

  it("projects a generated plan into the conversation", () => {
    const state = runReducer(initialRunState, {
      type: "event",
      event: event(1, "plan.created", { plan_text: "1. inspect\n2. verify" }),
    });
    expect(state.messages.at(-1)).toEqual({
      role: "assistant",
      content: "计划：\n1. inspect\n2. verify",
    });
  });
});
