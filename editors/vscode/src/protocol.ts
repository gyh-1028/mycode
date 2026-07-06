export const PROTOCOL_VERSION = 1;

export interface RpcRequest {
  jsonrpc: "2.0";
  id: number;
  method: string;
  params?: Record<string, unknown>;
}

export interface RpcResponse {
  jsonrpc: "2.0";
  id: number | null;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
}

export interface RpcNotification {
  jsonrpc: "2.0";
  method: string;
  params: Record<string, unknown>;
}

export interface AgentEvent {
  schema_version: number;
  run_id: string;
  seq: number;
  type: string;
  timestamp: number;
  payload: Record<string, unknown>;
}

export interface RunResult {
  run_id: string;
  sessionId: string;
  status: string;
  final_text?: string | null;
  error?: string | null;
  tracePath?: string | null;
  prompt_tokens?: number;
  completion_tokens?: number;
  cached_tokens?: number;
  cache_write_tokens?: number;
  estimated_cost?: number | null;
  steps_taken?: number;
  tool_calls?: number;
}

export function isNotification(value: unknown): value is RpcNotification {
  if (!value || typeof value !== "object") return false;
  const message = value as Record<string, unknown>;
  return message.jsonrpc === "2.0" && typeof message.method === "string" && !("id" in message);
}

export function parseProtocolLine(line: string): RpcResponse | RpcNotification {
  const value: unknown = JSON.parse(line);
  if (!value || typeof value !== "object" || (value as Record<string, unknown>).jsonrpc !== "2.0") {
    throw new Error("Invalid JSON-RPC message from MyCode");
  }
  return value as RpcResponse | RpcNotification;
}
