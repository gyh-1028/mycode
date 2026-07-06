import { AgentEvent, ChatMessage } from "./protocol";

export interface Activity {
  id: string;
  name: string;
  detail: string;
  status: "running" | "done" | "error";
  duration?: number;
}

export interface RunState {
  status: string;
  stream: string;
  messages: ChatMessage[];
  activities: Activity[];
  usage: { input: number; output: number; cached: number; cost?: number };
  context: string;
  capacity?: { used: number; limit: number; percent: number };
  compacted?: boolean;
}

export type RunAction =
  | { type: "load"; messages: ChatMessage[] }
  | { type: "user"; content: string }
  | { type: "event"; event: AgentEvent }
  | { type: "result"; status: string; error?: string }
  | { type: "compacted"; compacted: boolean }
  | { type: "reset" };

export const initialRunState: RunState = {
  status: "idle",
  stream: "",
  messages: [],
  activities: [],
  usage: { input: 0, output: 0, cached: 0 },
  context: "",
};

export function runReducer(state: RunState, action: RunAction): RunState {
  if (action.type === "reset") return initialRunState;
  if (action.type === "load") return { ...initialRunState, messages: action.messages };
  if (action.type === "user") {
    return { ...state, messages: [...state.messages, { role: "user", content: action.content }], stream: "", status: "running" };
  }
  if (action.type === "result") return { ...state, status: action.status };
  if (action.type === "compacted") return { ...state, compacted: action.compacted };
  const { event } = action;
  const payload = event.payload;
  if (event.type === "run.started") return { ...state, status: "running", stream: "", activities: [], context: "", capacity: undefined, compacted: false };
  if (event.type === "model.stream.text") return { ...state, stream: state.stream + String(payload.content ?? "") };
  if (event.type === "model.stream.end") {
    return state.stream
      ? { ...state, messages: [...state.messages, { role: "assistant", content: state.stream }], stream: "" }
      : state;
  }
  if (event.type === "plan.created") {
    const plan = String(payload.plan_text ?? "").trim();
    return plan
      ? { ...state, messages: [...state.messages, { role: "assistant", content: `计划：\n${plan}` }] }
      : state;
  }
  if (event.type === "tool.call.started") {
    return {
      ...state,
      activities: [...state.activities, {
        id: String(payload.tool_call_id ?? `${event.run_id}-${event.seq}`),
        name: String(payload.name ?? "tool"),
        detail: String(payload.args_preview ?? ""),
        status: "running",
      }],
    };
  }
  if (event.type === "tool.call.finished") {
    const activities = [...state.activities];
    let index = -1;
    for (let cursor = activities.length - 1; cursor >= 0; cursor -= 1) {
      if (activities[cursor].status === "running" && activities[cursor].name === payload.name) {
        index = cursor;
        break;
      }
    }
    if (index >= 0) activities[index] = {
      ...activities[index],
      status: payload.is_error ? "error" : "done",
      detail: `${payload.result_len ?? 0} 字符`,
      duration: typeof payload.duration_ms === "number" ? payload.duration_ms : undefined,
    };
    return { ...state, activities };
  }
  if (event.type === "usage.reported") {
    return { ...state, usage: {
      input: Number(payload.prompt_tokens ?? 0),
      output: Number(payload.completion_tokens ?? 0),
      cached: Number(payload.cached_tokens ?? 0),
      cost: typeof payload.estimated_cost === "number" ? payload.estimated_cost : undefined,
    } };
  }
  if (event.type === "context.selected") return { ...state, context: JSON.stringify(payload, null, 2) };
  if (event.type === "context.capacity") {
    const used = Number(payload.used_tokens ?? 0);
    const limit = Number(payload.limit ?? 0);
    const percent = Math.max(0, Math.min(100, Number(payload.percent ?? 0)));
    return { ...state, capacity: limit > 0 ? { used, limit, percent } : undefined };
  }
  if (event.type === "context.compacted") return { ...state, compacted: true };
  if (event.type === "run.cancelled") return { ...state, status: "cancelled" };
  if (event.type === "run.failed" || event.type === "model.call.error") return { ...state, status: "failed" };
  if (event.type === "run.finished") return { ...state, status: String(payload.status ?? "completed") };
  return state;
}
