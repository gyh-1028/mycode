export interface RpcRequest {
  jsonrpc: "2.0";
  id: number;
  method: string;
  params: Record<string, unknown>;
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

export interface SessionSummary {
  id: string;
  model: string;
  provider: string;
  created_at?: string;
  updated_at?: string;
  turns?: number;
  preview?: string;
  messages?: ChatMessage[];
}

export interface ChatMessage {
  role: "system" | "user" | "assistant" | "tool";
  content?: string | null;
}

export interface FileEntry {
  name: string;
  path: string;
  type: "directory" | "file";
  size?: number;
  language?: string;
}

export interface OpenFile {
  path: string;
  language: string;
  content: string;
  size: number;
  lines: number;
}

export interface CodeSelection {
  path: string;
  startLine: number;
  endLine: number;
  text: string;
}

export interface DiffFile {
  path: string;
  kind: string;
  diff: string;
}

export interface SessionDiff {
  checkpointId: string | null;
  files: DiffFile[];
  diff: string;
}

export interface ContextItem {
  path: string;
  symbol?: string | null;
  reason: string;
  score: number;
  start_line: number;
  end_line: number;
}

export interface ContextSelection {
  estimated_tokens: number;
  paths: string[];
  items: ContextItem[];
  degraded: string[];
}

export interface ModelProfile {
  name: string;
  provider: string;
  model: string;
  baseUrl?: string | null;
  apiKeyEnv?: string | null;
  maxTokens?: number | null;
  temperature?: number | null;
  topP?: number | null;
  thinking?: "enabled" | "disabled" | null;
  thinkingFormat?: string | null;
  thinkingBudget?: number | null;
  reasoningEffort?: string | null;
  active: boolean;
  keySource: string;
  keyConfigured: boolean;
}

export type ThinkingCapability = "toggle" | "effort" | "enabled" | "disabled" | "none";

export interface ModelOption {
  id: string;
  label: string;
  description: string;
  thinking: ThinkingCapability;
  reasoningEfforts?: string[];
  deprecated?: boolean;
}

export interface ThinkingBudgetConfig {
  min: number;
  default: number;
  step: number;
}

export interface ModelCatalog {
  id: string;
  label: string;
  provider: string;
  baseUrl: string;
  apiKeyEnv: string;
  defaultModel: string;
  thinkingFormat: string;
  thinkingBudget?: ThinkingBudgetConfig;
  reasoningEfforts: string[];
  models: ModelOption[];
}

export interface ModelFormState {
  catalogId: string;
  name: string;
  provider: string;
  model: string;
  baseUrl: string;
  apiKeyEnv: string;
  apiKey: string;
  thinking: "" | "enabled" | "disabled";
  thinkingFormat: string;
  thinkingBudget: string;
  reasoningEffort: string;
  maxTokens: string;
  temperature: string;
  topP: string;
}

export interface PermissionRequest {
  approvalId: string;
  runId?: string;
  kind: string;
  prompt: string;
  display_path?: string;
  diff?: string;
  command?: string;
  risk?: string;
  canRememberForRun?: boolean;
}

export type CollaborationMode = "default" | "plan" | "review";
export type PermissionMode = "standard" | "read-only" | "full-access";

export function isNotification(value: unknown): value is RpcNotification {
  if (!value || typeof value !== "object") return false;
  const message = value as Record<string, unknown>;
  return message.jsonrpc === "2.0" && typeof message.method === "string" && !("id" in message);
}
