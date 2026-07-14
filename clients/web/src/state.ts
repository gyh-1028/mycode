import { AgentEvent, ChatMessage, ContextSelection } from "./protocol";

export interface Activity {
  id: string;
  kind: "model" | "tool" | "system";
  name: string;
  summary: string;
  detail?: string;
  status: "running" | "done" | "error";
  duration?: number;
  step?: number;
}

export interface RunState {
  status: string;
  stream: string;
  messages: ChatMessage[];
  activities: Activity[];
  usage: { input: number; output: number; cached: number; cost?: number };
  context?: ContextSelection;
  capacity?: { used: number; limit: number; percent: number };
  compacted?: boolean;
  error?: string;
  plan?: string;
  planProgress?: string;
}

export type RunAction =
  | { type: "load"; messages: ChatMessage[] }
  | { type: "user"; content: string }
  | { type: "event"; event: AgentEvent }
  | { type: "result"; status: string; error?: string; finalText?: string }
  | { type: "compacted"; compacted: boolean }
  | { type: "reset" };

export const initialRunState: RunState = {
  status: "idle",
  stream: "",
  messages: [],
  activities: [],
  usage: { input: 0, output: 0, cached: 0 },
};

function finishLatestModel(activities: Activity[], status: "done" | "error", detail?: string): Activity[] {
  const next = [...activities];
  for (let index = next.length - 1; index >= 0; index -= 1) {
    if (next[index].kind === "model" && next[index].status === "running") {
      next[index] = { ...next[index], status, ...(detail ? { detail } : {}) };
      break;
    }
  }
  return next;
}

function appendFinalText(messages: ChatMessage[], finalText?: string): ChatMessage[] {
  const text = finalText?.trim();
  if (!text) return messages;
  const latestAssistant = [...messages].reverse().find((message) => message.role === "assistant" && message.content);
  if (latestAssistant?.content?.trim() === text) return messages;
  return [...messages, { role: "assistant", content: finalText }];
}

export function runReducer(state: RunState, action: RunAction): RunState {
  if (action.type === "reset") return initialRunState;
  if (action.type === "load") return { ...initialRunState, messages: action.messages };
  if (action.type === "user") {
    return {
      ...state,
      messages: [...state.messages, { role: "user", content: action.content }],
      stream: "",
      status: "running",
      error: undefined,
      plan: undefined,
      planProgress: undefined,
    };
  }
  if (action.type === "result") {
    return {
      ...state,
      status: action.status,
      error: action.error ?? (action.status === "completed" ? undefined : state.error),
      messages: appendFinalText(state.messages, action.finalText),
    };
  }
  if (action.type === "compacted") return { ...state, compacted: action.compacted };

  const { event } = action;
  const payload = event.payload;
  if (event.type === "run.started") {
    return {
      ...state,
      status: "running",
      stream: "",
      activities: [],
      context: undefined,
      capacity: undefined,
      compacted: false,
      error: undefined,
      plan: undefined,
      planProgress: undefined,
    };
  }
  if (event.type === "model.call.started") {
    const step = Number(payload.step ?? state.activities.length + 1);
    return {
      ...state,
      activities: [...state.activities, {
        id: `model-${event.run_id}-${step}-${event.seq}`,
        kind: "model",
        name: `模型调用 ${step}`,
        summary: "正在生成响应",
        status: "running",
        step,
      }],
    };
  }
  if (event.type === "model.call.retry") {
    const activities = [...state.activities];
    for (let index = activities.length - 1; index >= 0; index -= 1) {
      if (activities[index].kind === "model" && activities[index].status === "running") {
        activities[index] = {
          ...activities[index],
          summary: `重试 ${payload.attempt ?? "?"}/${payload.max_retries ?? "?"}`,
        };
        break;
      }
    }
    return { ...state, activities };
  }
  if (event.type === "model.stream.text") return { ...state, stream: state.stream + String(payload.content ?? "") };
  if (event.type === "model.stream.end") {
    return {
      ...state,
      activities: finishLatestModel(state.activities, "done"),
      ...(state.stream
        ? { messages: [...state.messages, { role: "assistant" as const, content: state.stream }], stream: "" }
        : {}),
    };
  }
  if (event.type === "model.call.error") {
    const detail = String(payload.detail ?? payload.reason ?? "模型调用失败");
    return { ...state, activities: finishLatestModel(state.activities, "error", detail), error: detail, status: "failed" };
  }
  if (event.type === "plan.created") {
    const plan = String(payload.plan_text ?? "").trim();
    return plan
      ? { ...state, plan, messages: [...state.messages, { role: "assistant", content: `计划：\n${plan}` }] }
      : state;
  }
  if (event.type === "plan.progress") return { ...state, planProgress: String(payload.line ?? "") };
  if (event.type === "tool.call.started") {
    const args = String(payload.args_preview ?? "");
    return {
      ...state,
      activities: [...state.activities, {
        id: String(payload.tool_call_id ?? `${event.run_id}-${event.seq}`),
        kind: "tool",
        name: String(payload.name ?? "tool"),
        summary: args || "无参数",
        detail: args,
        status: "running",
        step: typeof payload.step === "number" ? payload.step : undefined,
      }],
    };
  }
  if (event.type === "tool.call.finished") {
    const activities = [...state.activities];
    const toolCallId = String(payload.tool_call_id ?? "");
    let index = toolCallId ? activities.findIndex((item) => item.id === toolCallId) : -1;
    if (index < 0) {
      for (let cursor = activities.length - 1; cursor >= 0; cursor -= 1) {
        if (activities[cursor].status === "running" && activities[cursor].name === payload.name) {
          index = cursor;
          break;
        }
      }
    }
    if (index >= 0) {
      const isError = Boolean(payload.is_error);
      activities[index] = {
        ...activities[index],
        status: isError ? "error" : "done",
        summary: isError ? "执行失败" : `返回 ${payload.result_len ?? 0} 字符`,
        detail: isError
          ? String(payload.error_signature ?? activities[index].detail ?? "工具执行失败")
          : activities[index].detail,
        duration: typeof payload.duration_ms === "number" ? payload.duration_ms : undefined,
      };
    }
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
  if (event.type === "context.selected") return { ...state, context: payload as unknown as ContextSelection };
  if (event.type === "context.degraded") {
    const reason = String(payload.reason ?? "代码智能已降级");
    const context = state.context ?? { estimated_tokens: 0, paths: [], items: [], degraded: [] };
    return { ...state, context: { ...context, degraded: [...context.degraded, reason] } };
  }
  if (event.type === "context.capacity") {
    const used = Number(payload.used_tokens ?? 0);
    const limit = Number(payload.limit ?? 0);
    const percent = Math.max(0, Math.min(100, Number(payload.percent ?? 0)));
    return { ...state, capacity: limit > 0 ? { used, limit, percent } : undefined };
  }
  if (event.type === "context.compacted") return { ...state, compacted: true };
  if (event.type === "run.cancelled") return { ...state, status: "cancelled" };
  if (event.type === "run.failed") {
    return { ...state, status: "failed", error: String(payload.detail ?? payload.reason ?? "运行失败") };
  }
  if (event.type === "run.max_steps") return { ...state, status: "max_steps", error: "已达到最大执行步数，可缩小任务范围后继续" };
  if (event.type === "run.stuck") return { ...state, status: "stuck", error: `检测到重复操作，运行已停止：${String(payload.reason ?? "unknown")}` };
  if (event.type === "run.budget_exceeded") return { ...state, status: "budget_exceeded", error: "本次运行已达到费用预算" };
  if (event.type === "run.finished") return { ...state, status: String(payload.status ?? "completed") };
  return state;
}
