import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { AgentEvent, RpcNotification } from "../protocol.js";

declare function acquireVsCodeApi(): {
  postMessage(message: unknown): void;
  getState(): unknown;
  setState(state: unknown): void;
};

const vscode = acquireVsCodeApi();

type ChatMessage = { role: "user" | "assistant"; content: string };
type Activity = { id: string; name: string; detail: string; status: "running" | "done" | "error" };
type Permission = { approvalId: string; prompt: string; command?: string; diff?: string; risk?: string };

function App(): React.JSX.Element {
  const [model, setModel] = useState("--");
  const [status, setStatus] = useState("idle");
  const [sessionId, setSessionId] = useState<string>();
  const [runId, setRunId] = useState<string>();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [stream, setStream] = useState("");
  const [activities, setActivities] = useState<Activity[]>([]);
  const [permission, setPermission] = useState<Permission>();
  const [draft, setDraft] = useState("");
  const [usage, setUsage] = useState({ input: 0, output: 0, cached: 0, cost: undefined as number | undefined });
  const [activeTab, setActiveTab] = useState<"activity" | "diff">("activity");
  const [error, setError] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const streamRef = useRef("");

  useEffect(() => {
    const listener = (event: MessageEvent) => {
      const message = event.data as { type: string; value?: any };
      if (message.type === "initialized") {
        setModel(String(message.value?.model ?? "--"));
      } else if (message.type === "session") {
        const session = message.value;
        setSessionId(session?.id);
        setMessages((session?.messages ?? []).flatMap((item: any) => {
          if ((item.role === "user" || item.role === "assistant") && item.content) {
            return [{ role: item.role, content: String(item.content) } as ChatMessage];
          }
          return [];
        }));
        setActivities([]);
        streamRef.current = "";
        setStream("");
      } else if (message.type === "runStarted") {
        setRunId(message.value?.runId);
        setSessionId(message.value?.sessionId);
        setStatus("running");
      } else if (message.type === "draft") {
        setDraft(String(message.value ?? ""));
      } else if (message.type === "error") {
        setError(String(message.value ?? "Unknown error"));
        setStatus("failed");
      } else if (message.type === "notification") {
        handleNotification(message.value as RpcNotification);
      }
    };
    window.addEventListener("message", listener);
    vscode.postMessage({ type: "ready" });
    return () => window.removeEventListener("message", listener);
  }, []);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, stream, activities]);

  function handleNotification(notification: RpcNotification): void {
    if (notification.method === "agent/event") {
      handleAgentEvent(notification.params as unknown as AgentEvent);
    } else if (notification.method === "permission/request") {
      setPermission(notification.params as unknown as Permission);
      if ((notification.params as any).diff) setActiveTab("diff");
    } else if (notification.method === "run/result") {
      const result = notification.params as any;
      setStatus(String(result.status ?? "completed"));
      setRunId(undefined);
      if (result.error) setError(String(result.error));
      setPermission(undefined);
    }
  }

  function handleAgentEvent(event: AgentEvent): void {
    const payload = event.payload as any;
    if (event.type === "run.started") {
      setStatus("running");
      streamRef.current = "";
      setStream("");
      setActivities([]);
    } else if (event.type === "model.stream.text") {
      streamRef.current += String(payload.content ?? "");
      setStream(streamRef.current);
    } else if (event.type === "model.stream.end") {
      const completed = streamRef.current;
      if (completed) setMessages((items) => [...items, { role: "assistant", content: completed }]);
      streamRef.current = "";
      setStream("");
    } else if (event.type === "tool.call.started") {
      setActivities((items) => [...items, {
        id: `${event.run_id}-${event.seq}`,
        name: String(payload.name ?? "tool"),
        detail: String(payload.args_preview ?? ""),
        status: "running",
      }]);
    } else if (event.type === "tool.call.finished") {
      setActivities((items) => {
        const copy = [...items];
        let index = -1;
        for (let cursor = copy.length - 1; cursor >= 0; cursor -= 1) {
          if (copy[cursor].name === payload.name && copy[cursor].status === "running") {
            index = cursor;
            break;
          }
        }
        if (index >= 0) copy[index] = { ...copy[index], detail: `${payload.result_len ?? 0} chars`, status: payload.is_error ? "error" : "done" };
        return copy;
      });
    } else if (event.type === "usage.reported") {
      setUsage({ input: payload.prompt_tokens ?? 0, output: payload.completion_tokens ?? 0, cached: payload.cached_tokens ?? 0, cost: payload.estimated_cost });
    } else if (event.type === "run.cancelled") {
      setStatus("cancelled");
    } else if (event.type === "model.call.error" || event.type === "run.failed") {
      setStatus("failed");
      setError(String(payload.detail ?? payload.reason ?? "Run failed"));
    }
  }

  function submit(): void {
    const prompt = draft.trim();
    if (!prompt || status === "running") return;
    setMessages((items) => [...items, { role: "user", content: prompt }]);
    setDraft("");
    setError("");
    vscode.postMessage({ type: "startRun", prompt, sessionId });
  }

  function permissionDecision(approved: boolean): void {
    if (!permission) return;
    vscode.postMessage({ type: "permissionResponse", approvalId: permission.approvalId, approved });
    setPermission(undefined);
  }

  const cost = usage.cost === undefined || usage.cost === null ? "--" : `$${usage.cost.toFixed(6)}`;

  return <main className="app-shell">
    <header className="topbar">
      <div className="topbar-left">
        <span className="brand">MyCode</span>
        <span className="model-pill">{model}</span>
        <span className={`status-pill status-${status}`}>
          <span className="status-dot" />
          {status}
        </span>
      </div>
      <button className="icon-button" title="New session" aria-label="New session" onClick={() => vscode.postMessage({ type: "newSession" })}><i className="codicon codicon-add" /></button>
    </header>

    <section className="conversation" aria-live="polite">
      {messages.length === 0 && <div className="empty-state"><i className="codicon codicon-terminal" /><strong>Start a coding task</strong><span>Changes and commands still require your configured approval.</span></div>}
      {messages.map((message, index) => <article className={`message message-${message.role}`} key={index}>
        <span className="message-label">{message.role === "user" ? "You" : "MyCode"}</span>
        <pre>{message.content}</pre>
      </article>)}
      {stream && <article className="message message-assistant streaming"><span className="message-label">MyCode</span><pre>{stream}</pre></article>}
      {error && <div className="error-row"><i className="codicon codicon-error" />{error}</div>}
      <div ref={bottomRef} />
    </section>

    <section className="details">
      <div className="tabbar" role="tablist">
        <button className={activeTab === "activity" ? "active" : ""} onClick={() => setActiveTab("activity")}>Activity <span>{activities.length}</span></button>
        <button className={activeTab === "diff" ? "active" : ""} onClick={() => setActiveTab("diff")}>Diff</button>
      </div>
      {activeTab === "activity" ? <div className="activity-list">
        {activities.length === 0 ? <span className="muted">No tool activity</span> : activities.map((item) => <div className="activity-row" key={item.id}>
          <i className={`codicon ${item.status === "running" ? "codicon-loading codicon-modifier-spin" : item.status === "error" ? "codicon-error" : "codicon-check"}`} />
          <div><strong>{item.name}</strong><code>{item.detail}</code></div>
          <span className="activity-status">{item.status}</span>
        </div>)}
      </div> : <pre className="diff-view">{permission?.diff || "No diff available"}</pre>}
    </section>

    {permission && <section className="permission-bar" role="alertdialog" aria-label="Permission request">
      <div><strong>{permission.prompt}</strong><code>{permission.command || permission.diff?.split("\n")[0] || ""}</code></div>
      <div className="permission-actions"><button onClick={() => permissionDecision(false)}>Deny</button><button className="primary" onClick={() => permissionDecision(true)}>Approve</button></div>
    </section>}

    <section className="composer">
      <textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); submit(); } }} placeholder="Describe a task (Ctrl+Enter)" rows={3} />
      <button className="icon-button run-button" title={status === "running" ? "Cancel run" : "Run"} aria-label={status === "running" ? "Cancel run" : "Run"} onClick={() => status === "running" ? vscode.postMessage({ type: "cancelRun", runId }) : submit()}>
        <i className={`codicon ${status === "running" ? "codicon-debug-stop" : "codicon-send"}`} />
      </button>
    </section>

    <footer className="statusbar"><span>{model}</span><span>{usage.input} in / {usage.output} out</span><span>{usage.cached} cached</span><span>{cost}</span></footer>
  </main>;
}

createRoot(document.getElementById("root")!).render(<App />);
