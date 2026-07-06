import {
  Activity as ActivityIcon,
  Bot,
  Check,
  ChevronDown,
  CircleStop,
  ClipboardCheck,
  Code2,
  FileDiff,
  FileSearch,
  Files,
  FolderSearch2,
  LoaderCircle,
  LockKeyhole,
  ListTodo,
  Menu,
  MessageSquare,
  Minimize2,
  Moon,
  PanelRight,
  Plus,
  Search,
  Send,
  Settings,
  ShieldCheck,
  Sun,
  Trash2,
  TriangleAlert,
  X,
  XCircle,
} from "lucide-react";
import { FormEvent, lazy, Suspense, useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { FileTree } from "./FileTree";
import { ModelSettingsDialog } from "./ModelSettingsDialog";
import {
  AgentEvent,
  CollaborationMode,
  FileEntry,
  ModelCatalog,
  ModelFormState,
  ModelProfile,
  OpenFile,
  PermissionRequest,
  PermissionMode,
  RpcNotification,
  SessionSummary,
} from "./protocol";
import { RpcClient } from "./rpc";
import { initialRunState, runReducer } from "./state";

type Theme = "system" | "dark" | "light";
type SidebarMode = "sessions" | "files" | "search";
type InspectorTab = "activity" | "diff" | "context";

interface InitializeResult { workspace: string; model: string; provider: string }
interface SessionResult { session: SessionSummary }
interface ModelListResult { active?: string; profiles: ModelProfile[]; presets: ModelProfile[]; catalogs: ModelCatalog[] }

const STATUS_LABELS: Record<string, string> = {
  idle: "就绪", running: "运行中", completed: "已完成", failed: "失败", cancelled: "已取消",
};

const CodeViewer = lazy(() => import("./CodeViewer").then((module) => ({ default: module.CodeViewer })));

const EMPTY_MODEL_FORM: ModelFormState = {
  catalogId: "",
  name: "",
  provider: "openai",
  model: "",
  baseUrl: "",
  apiKeyEnv: "",
  apiKey: "",
  thinking: "",
  thinkingFormat: "",
  thinkingBudget: "",
  reasoningEffort: "",
};

function basename(path: string): string {
  return path.replaceAll("\\", "/").split("/").filter(Boolean).at(-1) ?? path;
}

export default function App(): React.JSX.Element {
  const rpc = useMemo(() => new RpcClient(), []);
  const [connected, setConnected] = useState(false);
  const [fatalError, setFatalError] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [model, setModel] = useState("--");
  const [provider, setProvider] = useState("--");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionId, setSessionId] = useState<string>();
  const [runId, setRunId] = useState<string>();
  const [run, dispatch] = useReducer(runReducer, initialRunState);
  const [permission, setPermission] = useState<PermissionRequest>();
  const [collaborationMode, setCollaborationMode] = useState<CollaborationMode>(
    () => (localStorage.getItem("mycode-collaboration-mode") as CollaborationMode | null) ?? "default",
  );
  const [permissionMode, setPermissionMode] = useState<PermissionMode>(
    () => (localStorage.getItem("mycode-permission-mode") as PermissionMode | null) ?? "standard",
  );
  const [fullAccessWarning, setFullAccessWarning] = useState(false);
  const [diff, setDiff] = useState("");
  const [sidebarMode, setSidebarMode] = useState<SidebarMode>("sessions");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("activity");
  const [rootFiles, setRootFiles] = useState<FileEntry[]>([]);
  const [openFiles, setOpenFiles] = useState<OpenFile[]>([]);
  const [activeFile, setActiveFile] = useState<string>();
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<Array<{ path: string; line: number; preview: string }>>([]);
  const [draft, setDraft] = useState("");
  const [theme, setTheme] = useState<Theme>(() => (localStorage.getItem("mycode-theme") as Theme | null) ?? "system");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [models, setModels] = useState<ModelProfile[]>([]);
  const [presets, setPresets] = useState<ModelProfile[]>([]);
  const [catalogs, setCatalogs] = useState<ModelCatalog[]>([]);
  const [modelForm, setModelForm] = useState<ModelFormState>(EMPTY_MODEL_FORM);
  const [pendingDelete, setPendingDelete] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [compacting, setCompacting] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const refreshSessions = useCallback(async () => {
    const result = await rpc.request<{ sessions: SessionSummary[] }>("session/list");
    setSessions(result.sessions);
  }, [rpc]);

  const refreshModels = useCallback(async () => {
    const result = await rpc.request<ModelListResult>("model/list");
    setModels(result.profiles);
    setPresets(result.presets);
    setCatalogs(result.catalogs);
  }, [rpc]);

  const loadDirectory = useCallback(async (path: string) => {
    const result = await rpc.request<{ entries: FileEntry[] }>("workspace/list", { path });
    return result.entries;
  }, [rpc]);

  const loadDiff = useCallback(async (id?: string) => {
    if (!id) return;
    const result = await rpc.request<{ diff: string }>("session/diff", { sessionId: id });
    setDiff(result.diff);
  }, [rpc]);

  useEffect(() => {
    let active = true;
    const unsubscribe = rpc.onNotification((notification) => {
      if (!active) return;
      handleNotification(notification);
    });
    void (async () => {
      try {
        await rpc.connect();
        const initialized = await rpc.request<InitializeResult>("initialize", {});
        if (!active) return;
        setWorkspace(initialized.workspace);
        setModel(initialized.model);
        setProvider(initialized.provider);
        setConnected(true);
        const [sessionResult, files, modelResult] = await Promise.all([
          rpc.request<{ sessions: SessionSummary[] }>("session/list"),
          rpc.request<{ entries: FileEntry[] }>("workspace/list", { path: "." }),
          rpc.request<ModelListResult>("model/list"),
        ]);
        if (!active) return;
        setSessions(sessionResult.sessions);
        setRootFiles(files.entries);
        setModels(modelResult.profiles);
        setPresets(modelResult.presets);
        setCatalogs(modelResult.catalogs);
        if (sessionResult.sessions[0]) await openSession(sessionResult.sessions[0].id);
        else await newSession();
      } catch (error) {
        if (active) setFatalError(error instanceof Error ? error.message : String(error));
      }
    })();
    return () => { active = false; unsubscribe(); rpc.close(); };
  }, [rpc]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("mycode-theme", theme);
  }, [theme]);

  useEffect(() => {
    localStorage.setItem("mycode-collaboration-mode", collaborationMode);
  }, [collaborationMode]);

  useEffect(() => {
    localStorage.setItem("mycode-permission-mode", permissionMode);
  }, [permissionMode]);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [run.messages, run.stream]);

  function handleNotification(notification: RpcNotification): void {
    if (notification.method === "agent/event") {
      dispatch({ type: "event", event: notification.params as unknown as AgentEvent });
      return;
    }
    if (notification.method === "permission/request") {
      const request = notification.params as unknown as PermissionRequest;
      setPermission(request);
      if (request.diff) {
        setDiff(request.diff);
      }
      return;
    }
    if (notification.method === "run/result") {
      const result = notification.params;
      dispatch({ type: "result", status: String(result.status ?? "completed"), error: result.error ? String(result.error) : undefined });
      setRunId(undefined);
      setPermission(undefined);
      const id = String(result.sessionId ?? sessionId ?? "");
      void Promise.all([refreshSessions(), loadDiff(id)]);
    }
  }

  async function newSession(): Promise<void> {
    const result = await rpc.request<SessionResult>("session/new");
    setSessionId(result.session.id);
    dispatch({ type: "load", messages: result.session.messages ?? [] });
    setActiveFile(undefined);
    setDiff("");
    await refreshSessions();
  }

  async function openSession(id: string): Promise<void> {
    const result = await rpc.request<SessionResult>("session/open", { sessionId: id });
    setSessionId(id);
    dispatch({ type: "load", messages: result.session.messages ?? [] });
    setActiveFile(undefined);
    await loadDiff(id);
    setSidebarOpen(false);
  }

  async function deleteSession(id: string): Promise<void> {
    await rpc.request("session/delete", { sessionId: id });
    setPendingDelete(undefined);
    if (sessionId === id) await newSession();
    else await refreshSessions();
  }

  async function submit(): Promise<void> {
    const prompt = draft.trim();
    if (!prompt || run.status === "running") return;
    setDraft("");
    dispatch({ type: "user", content: prompt });
    setActiveFile(undefined);
    try {
      const result = await rpc.request<{ runId: string; sessionId: string }>("run/start", {
        prompt,
        collaborationMode,
        permissionMode,
        ...(sessionId ? { sessionId } : {}),
      });
      setRunId(result.runId);
      setSessionId(result.sessionId);
    } catch (error) {
      dispatch({ type: "result", status: "failed", error: error instanceof Error ? error.message : String(error) });
      setFatalError(error instanceof Error ? error.message : String(error));
    }
  }

  async function cancelRun(): Promise<void> {
    await rpc.request("run/cancel", runId ? { runId } : {});
  }

  async function compactContext(): Promise<void> {
    if (!sessionId || run.status === "running" || compacting) return;
    setCompacting(true);
    try {
      const result = await rpc.request<{ compacted: boolean; sessionId: string }>("session/compact", { sessionId });
      if (result.compacted) {
        await openSession(result.sessionId);
        dispatch({ type: "compacted", compacted: true });
      }
    } catch (error) {
      setFatalError(error instanceof Error ? error.message : String(error));
    } finally {
      setCompacting(false);
    }
  }

  async function decidePermission(approved: boolean): Promise<void> {
    if (!permission) return;
    await rpc.request("permission/respond", { approvalId: permission.approvalId, approved });
    setPermission(undefined);
  }

  function choosePermissionMode(next: PermissionMode): void {
    if (next === "full-access" && permissionMode !== "full-access") {
      setFullAccessWarning(true);
      return;
    }
    setPermissionMode(next);
  }

  async function openFile(path: string): Promise<void> {
    const existing = openFiles.find((file) => file.path === path);
    if (existing) {
      setActiveFile(path);
      return;
    }
    try {
      const file = await rpc.request<OpenFile>("workspace/read", { path });
      setOpenFiles((items) => [...items, file]);
      setActiveFile(path);
      setSidebarOpen(false);
    } catch (error) {
      setFatalError(error instanceof Error ? error.message : String(error));
    }
  }

  function closeFile(path: string): void {
    setOpenFiles((items) => items.filter((file) => file.path !== path));
    if (activeFile === path) setActiveFile(undefined);
  }

  async function searchWorkspace(event: FormEvent): Promise<void> {
    event.preventDefault();
    if (!searchQuery.trim()) return;
    const result = await rpc.request<{ matches: Array<{ path: string; line: number; preview: string }> }>("workspace/search", { query: searchQuery.trim(), limit: 200 });
    setSearchResults(result.matches);
  }

  function chooseProvider(id: string): void {
    const catalog = catalogs.find((item) => item.id === id);
    if (catalog) {
      const selected = catalog.models.find((item) => item.id === catalog.defaultModel);
      const fixedThinking = selected?.thinking === "enabled" || selected?.thinking === "disabled" ? selected.thinking : "";
      setModelForm({
        catalogId: id,
        name: catalog.defaultModel,
        provider: catalog.provider,
        model: catalog.defaultModel,
        baseUrl: catalog.baseUrl,
        apiKeyEnv: catalog.apiKeyEnv,
        apiKey: "",
        thinking: fixedThinking,
        thinkingFormat: catalog.thinkingFormat,
        thinkingBudget: "",
        reasoningEffort: "",
      });
      return;
    }
    const presetName = id.startsWith("preset:") ? id.slice(7) : "";
    const preset = presets.find((item) => item.name === presetName);
    if (preset) {
      setModelForm({
        catalogId: id,
        name: preset.name,
        provider: preset.provider,
        model: preset.model,
        baseUrl: preset.baseUrl ?? "",
        apiKeyEnv: preset.apiKeyEnv ?? "",
        apiKey: "",
        thinking: preset.thinking ?? "",
        thinkingFormat: preset.thinkingFormat ?? "",
        thinkingBudget: preset.thinkingBudget == null ? "" : String(preset.thinkingBudget),
        reasoningEffort: preset.reasoningEffort ?? "",
      });
      return;
    }
    setModelForm(EMPTY_MODEL_FORM);
  }

  function chooseModel(modelId: string): void {
    const selected = catalogs.find((item) => item.id === modelForm.catalogId)?.models.find((item) => item.id === modelId);
    const fixedThinking = selected?.thinking === "enabled" || selected?.thinking === "disabled" ? selected.thinking : "";
    setModelForm((value) => ({
      ...value,
      name: modelId,
      model: modelId,
      thinking: fixedThinking,
      thinkingFormat: catalogs.find((item) => item.id === value.catalogId)?.thinkingFormat ?? value.thinkingFormat,
      thinkingBudget: "",
      reasoningEffort: "",
    }));
  }

  async function saveModel(event: FormEvent): Promise<void> {
    event.preventDefault();
    setBusy(true);
    try {
      await rpc.request("model/save", {
        profile: {
          name: modelForm.name,
          provider: modelForm.provider,
          model: modelForm.model,
          baseUrl: modelForm.baseUrl || null,
          apiKeyEnv: modelForm.apiKeyEnv || null,
          thinking: modelForm.thinking || null,
          thinkingFormat: modelForm.thinkingFormat || null,
          thinkingBudget: modelForm.thinkingBudget === "" ? null : Number(modelForm.thinkingBudget),
          reasoningEffort: modelForm.reasoningEffort || null,
        },
        ...(modelForm.apiKey ? { apiKey: modelForm.apiKey } : {}),
        replace: true,
      });
      setModelForm((value) => ({ ...value, apiKey: "" }));
      await refreshModels();
    } finally {
      setBusy(false);
    }
  }

  async function useModel(name: string): Promise<void> {
    setBusy(true);
    try {
      const result = await rpc.request<{ runtime: InitializeResult; session: SessionSummary }>("model/use", { name });
      setModel(result.runtime.model);
      setProvider(result.runtime.provider);
      setSessionId(result.session.id);
      dispatch({ type: "load", messages: result.session.messages ?? [] });
      setSettingsOpen(false);
      await Promise.all([refreshModels(), refreshSessions()]);
    } finally {
      setBusy(false);
    }
  }

  async function removeModel(name: string): Promise<void> {
    setBusy(true);
    try {
      await rpc.request("model/remove", { name, deleteKey: true });
      setPendingDelete(undefined);
      await refreshModels();
    } finally {
      setBusy(false);
    }
  }

  const selectedFile = openFiles.find((file) => file.path === activeFile);
  const cost = run.usage.cost === undefined ? "--" : `$${run.usage.cost.toFixed(6)}`;

  if (fatalError && !connected) return <main className="fatal-screen"><Bot size={34} /><h1>无法启动 MyCode Web</h1><p>{fatalError}</p><p>请关闭此页面，在终端检查服务输出后重新运行。</p></main>;

  return <main className={`app-shell ${sidebarOpen ? "sidebar-open" : ""} ${inspectorOpen ? "inspector-open" : ""}`}>
    <header className="topbar">
      <button className="icon-button mobile-menu" onClick={() => setSidebarOpen(!sidebarOpen)} title="打开导航"><Menu size={18} /></button>
      <div className="brand-badge"><span className="brand-mark"><Code2 size={17} /></span><span className="brand-wordmark">MyCode</span></div>
      <div className="workspace-name"><span>{basename(workspace)}</span><small>{workspace}</small></div>
      <button className="model-pill" onClick={() => setSettingsOpen(true)}><Bot size={15} /><span>{model}</span><ChevronDown size={13} /></button>
      <label className={`permission-select permission-${permissionMode}`} title="设置本次运行的权限模式">
        {permissionMode === "read-only" ? <LockKeyhole size={14} /> : <ShieldCheck size={14} />}
        <select
          aria-label="权限模式"
          disabled={run.status === "running"}
          value={permissionMode}
          onChange={(event) => choosePermissionMode(event.target.value as PermissionMode)}
        >
          <option value="standard">标准确认</option>
          <option value="read-only">只读</option>
          <option value="full-access">完全信任</option>
        </select>
        <ChevronDown size={12} />
      </label>
      <span className={`run-status status-${run.status}`}><span className="status-dot" /><span className="status-label">{STATUS_LABELS[run.status] ?? run.status}</span></span>
      <button className="icon-button" onClick={() => setTheme(theme === "dark" ? "light" : theme === "light" ? "system" : "dark")} title={`主题：${theme}`}>
        {theme === "dark" ? <Moon size={17} /> : <Sun size={17} />}
      </button>
      <button className="icon-button" onClick={() => setSettingsOpen(true)} title="模型与设置"><Settings size={17} /></button>
      <button className="icon-button mobile-inspector" onClick={() => setInspectorOpen(!inspectorOpen)} title="运行详情"><PanelRight size={17} /></button>
    </header>

    <nav className="nav-rail" aria-label="主导航">
      <button className={sidebarMode === "sessions" ? "active" : ""} onClick={() => { setSidebarMode("sessions"); setSidebarOpen(true); }} title="会话"><MessageSquare size={19} /></button>
      <button className={sidebarMode === "files" ? "active" : ""} onClick={() => { setSidebarMode("files"); setSidebarOpen(true); }} title="文件"><Files size={19} /></button>
      <button className={sidebarMode === "search" ? "active" : ""} onClick={() => { setSidebarMode("search"); setSidebarOpen(true); }} title="搜索"><FileSearch size={19} /></button>
      <span className="rail-spacer" />
      <button onClick={() => setSettingsOpen(true)} title="设置"><Settings size={19} /></button>
    </nav>

    <aside className="sidebar">
      <div className="panel-heading"><strong>{sidebarMode === "sessions" ? "会话" : sidebarMode === "files" ? "文件" : "搜索"}</strong><button className="icon-button close-panel" onClick={() => setSidebarOpen(false)}><X size={16} /></button></div>
      {sidebarMode === "sessions" && <>
        <button className="new-task" onClick={() => void newSession()} disabled={run.status === "running"}><Plus size={16} />新建任务</button>
        <div className="session-list">
          {sessions.map((session) => <div className={`session-row ${session.id === sessionId ? "selected" : ""}`} key={session.id}>
            <button onClick={() => void openSession(session.id)}><strong>{session.preview || "新会话"}</strong><span>{session.turns ?? 0} 轮 · {session.model}</span></button>
            <button className="row-action" onClick={() => setPendingDelete(session.id)} title="删除会话"><Trash2 size={14} /></button>
            {pendingDelete === session.id && <div className="inline-confirm"><span>确认删除？</span><button onClick={() => setPendingDelete(undefined)}>取消</button><button className="danger" onClick={() => void deleteSession(session.id)}>删除</button></div>}
          </div>)}
        </div>
      </>}
      {sidebarMode === "files" && <FileTree entries={rootFiles} load={loadDirectory} open={(path) => void openFile(path)} activePath={activeFile} />}
      {sidebarMode === "search" && <>
        <form className="search-form" onSubmit={(event) => void searchWorkspace(event)}><Search size={15} /><input value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} placeholder="搜索项目内容" /><button type="submit">搜索</button></form>
        <div className="search-results">{searchResults.map((result) => <button key={`${result.path}:${result.line}`} onClick={() => void openFile(result.path)}><strong>{result.path}:{result.line}</strong><span>{result.preview}</span></button>)}</div>
      </>}
    </aside>

    <section className="workspace">
      <div className="document-tabs">
        <button className={!activeFile ? "active" : ""} onClick={() => setActiveFile(undefined)}><MessageSquare size={14} />对话</button>
        {openFiles.map((file) => <button className={activeFile === file.path ? "active" : ""} key={file.path} onClick={() => setActiveFile(file.path)}><FileCodeIcon /><span>{basename(file.path)}</span><i onClick={(event) => { event.stopPropagation(); closeFile(file.path); }}><X size={12} /></i></button>)}
      </div>
      {selectedFile ? <div className="file-surface"><div className="file-meta"><span>{selectedFile.path}</span><span>{selectedFile.lines} 行 · 只读</span></div><Suspense fallback={<div className="code-loading"><LoaderCircle className="spin" size={18} />正在加载代码预览</div>}><CodeViewer file={selectedFile} /></Suspense></div> : <>
        <div className="conversation" aria-live="polite">
          {run.messages.filter((message) => (message.role === "user" || message.role === "assistant") && message.content).length === 0 && <div className="empty-state"><Bot size={30} /><h2>开始一个编码任务</h2><p>描述目标，MyCode 会读取项目、提出修改并在执行前请求确认。</p></div>}
          {run.messages.map((message, index) => (message.role === "user" || message.role === "assistant") && message.content ? <article className={`message message-${message.role}`} key={index}><header>{message.role === "user" ? "你" : "MyCode"}</header><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>{message.content}</ReactMarkdown></article> : null)}
          {run.stream && <article className="message message-assistant streaming"><header>MyCode <LoaderCircle size={13} className="spin" /></header><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>{run.stream}</ReactMarkdown></article>}
          {fatalError && connected && <div className="inline-error"><XCircle size={16} />{fatalError}<button onClick={() => setFatalError("")}><X size={14} /></button></div>}
          <div ref={bottomRef} />
        </div>
        <div className="composer-wrap">
          <div className="composer">
            <textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); void submit(); } }} placeholder={collaborationMode === "plan" ? "描述需要规划的任务…" : collaborationMode === "review" ? "描述需要审查的范围…" : "描述你想完成的任务…"} rows={3} />
            <button className={`send-button ${run.status === "running" ? "stop" : ""}`} onClick={() => run.status === "running" ? void cancelRun() : void submit()} title={run.status === "running" ? "停止运行" : "发送"}>{run.status === "running" ? <CircleStop size={18} /> : <Send size={18} />}</button>
          </div>
          <div className="composer-footer">
            <div className="collaboration-control" aria-label="工作模式">
              <button className={collaborationMode === "default" ? "active" : ""} disabled={run.status === "running"} onClick={() => setCollaborationMode("default")} title="执行任务并按需修改项目"><Bot size={13} />执行</button>
              <button className={collaborationMode === "plan" ? "active" : ""} disabled={run.status === "running"} onClick={() => setCollaborationMode("plan")} title="只生成计划，不执行修改"><ListTodo size={13} />计划</button>
              <button className={collaborationMode === "review" ? "active" : ""} disabled={run.status === "running"} onClick={() => setCollaborationMode("review")} title="只读审查当前改动"><ClipboardCheck size={13} />审查</button>
            </div>
            <span className="composer-hint">Ctrl + Enter 发送</span>
          </div>
        </div>
      </>}
    </section>

    <aside className="inspector">
      <div className="inspector-tabs">
        <button className={inspectorTab === "activity" ? "active" : ""} onClick={() => setInspectorTab("activity")}><ActivityIcon size={14} />活动</button>
        <button className={inspectorTab === "diff" ? "active" : ""} onClick={() => setInspectorTab("diff")}><FileDiff size={14} />Diff</button>
        <button className={inspectorTab === "context" ? "active" : ""} onClick={() => setInspectorTab("context")}><FolderSearch2 size={14} />上下文</button>
        <button className="icon-button close-panel" onClick={() => setInspectorOpen(false)}><X size={16} /></button>
      </div>
      <div className="inspector-content">
        {inspectorTab === "activity" && <div className="timeline">{run.activities.length === 0 ? <div className="panel-empty">暂无工具活动</div> : run.activities.map((activity) => <div className={`timeline-row ${activity.status}`} key={activity.id}>{activity.status === "running" ? <LoaderCircle size={15} className="spin" /> : activity.status === "error" ? <XCircle size={15} /> : <Check size={15} />}<div><strong>{activity.name}</strong><code>{activity.detail}</code></div><span>{activity.duration ? `${activity.duration}ms` : ""}</span></div>)}</div>}
        {inspectorTab === "diff" && <pre className="diff-view">{diff || "当前会话没有文件修改"}</pre>}
        {inspectorTab === "context" && (
          <div className="context-panel">
            <div className="context-toolbar">
              <button
                className="compact-button"
                disabled={run.status === "running" || compacting || !sessionId}
                onClick={() => void compactContext()}
                title="手动压缩较早的对话上下文"
              >
                {compacting ? <LoaderCircle size={13} className="spin" /> : <Minimize2 size={13} />}
                压缩上下文
              </button>
              {run.compacted && <span className="compacted-badge">已压缩</span>}
            </div>
            <pre className="context-view">{run.context || "本轮尚未选择自动上下文"}</pre>
          </div>
        )}
      </div>
    </aside>

    {(sidebarOpen || inspectorOpen) && <button className="panel-overlay" onClick={() => { setSidebarOpen(false); setInspectorOpen(false); }} aria-label="关闭面板" />}

    <footer className="statusbar"><span>{provider}</span><span>{model}</span><span>{collaborationMode === "default" ? "执行" : collaborationMode === "plan" ? "计划" : "审查"}</span><span>{run.usage.input} 输入 / {run.usage.output} 输出</span><span>{run.usage.cached} 缓存</span><span>{cost}</span>{run.capacity && (
      <span className="capacity-bar" title={`上下文: ${run.capacity.used} / ${run.capacity.limit} tokens (${run.capacity.percent}%)`}>
        <span
          className={`capacity-fill ${run.capacity.percent >= 70 ? "capacity-high" : run.capacity.percent >= 50 ? "capacity-medium" : ""}`}
          style={{ width: `${run.capacity.percent}%` }}
        />
        <span className="capacity-label">{run.capacity.percent}%</span>
      </span>
    )}<span className="status-spacer" /><span className={`statusbar-pill ${connected ? "connected" : "connecting"}`}>{connected ? "仅本机" : "连接中"}</span></footer>

    {permission && <ApprovalDialog request={permission} onDecision={(approved) => void decidePermission(approved)} />}

    {fullAccessWarning && <div className="modal-backdrop permission-warning-backdrop" role="presentation">
      <section className="permission-warning" role="alertdialog" aria-modal="true" aria-label="启用完全信任">
        <TriangleAlert size={22} />
        <div><h2>启用完全信任？</h2><p>后续写文件、运行命令和调用 MCP 不再逐次确认。项目根限制、敏感文件规则和危险命令黑名单仍然生效，但它们不是安全沙箱。</p></div>
        <div className="warning-actions"><button onClick={() => setFullAccessWarning(false)}>取消</button><button className="danger" onClick={() => { setPermissionMode("full-access"); setFullAccessWarning(false); }}>确认启用</button></div>
      </section>
    </div>}

    {settingsOpen && <ModelSettingsDialog
      profiles={models}
      presets={presets}
      catalogs={catalogs}
      form={modelForm}
      busy={busy}
      pendingDelete={pendingDelete}
      onClose={() => setSettingsOpen(false)}
      onFormChange={setModelForm}
      onProviderChange={chooseProvider}
      onModelChange={chooseModel}
      onSave={(event) => void saveModel(event)}
      onUse={(name) => void useModel(name)}
      onDeleteRequest={setPendingDelete}
      onRemove={(name) => void removeModel(name)}
    />}
  </main>;
}

function FileCodeIcon(): React.JSX.Element {
  return <Code2 size={14} />;
}

function ApprovalDialog({ request, onDecision }: { request: PermissionRequest; onDecision: (approved: boolean) => void }): React.JSX.Element {
  const isWrite = request.kind === "write";
  return <div className="approval-backdrop" role="presentation">
    <section className="approval-dialog" role="alertdialog" aria-modal="true" aria-label="操作确认">
      <header>
        <span className="approval-icon">{isWrite ? <FileDiff size={18} /> : <ActivityIcon size={18} />}</span>
        <div><h2>{isWrite ? "确认文件修改" : "确认执行命令"}</h2><p>{request.prompt}</p></div>
        <span className="approval-risk">{request.risk || request.kind}</span>
      </header>
      {request.display_path && <div className="approval-path">{request.display_path}</div>}
      {request.command && <pre className="approval-content command-content">{request.command}</pre>}
      {request.diff && <pre className="approval-content diff-content">{request.diff}</pre>}
      <footer><span>仅允许本次操作</span><div><button onClick={() => onDecision(false)}>拒绝</button><button className="primary" onClick={() => onDecision(true)}>允许本次</button></div></footer>
    </section>
  </div>;
}
