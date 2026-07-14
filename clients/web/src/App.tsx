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
  Play,
  Paperclip,
  RefreshCw,
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
  CodeSelection,
  CollaborationMode,
  FileEntry,
  ModelCatalog,
  ModelFormState,
  ModelProfile,
  OpenFile,
  PermissionRequest,
  PermissionMode,
  RpcNotification,
  SessionDiff,
  SessionSummary,
} from "./protocol";
import { ConnectionStatus, RpcClient } from "./rpc";
import { InspectorTab, RunInspector } from "./RunInspector";
import { initialRunState, runReducer } from "./state";

type Theme = "system" | "dark" | "light";
type SidebarMode = "sessions" | "files" | "search";

interface PromptAttachment {
  id: string;
  label: string;
  promptText: string;
}

interface InitializeResult { workspace: string; model: string; provider: string }
interface SessionResult { session: SessionSummary }
interface ModelListResult {
  active?: string;
  profiles: ModelProfile[];
  presets: ModelProfile[];
  catalogs: ModelCatalog[];
  catalogMetadata?: {
    schema_version: number;
    catalog_version: string;
    verified_at?: string;
    pricing_verified_at?: string;
  };
}

const STATUS_LABELS: Record<string, string> = {
  idle: "就绪", running: "运行中", completed: "已完成", failed: "失败", cancelled: "已取消",
  max_steps: "达到步数上限", stuck: "已停止", budget_exceeded: "达到预算", model_error: "模型错误",
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
  maxTokens: "",
  temperature: "",
  topP: "",
};

function basename(path: string): string {
  return path.replaceAll("\\", "/").split("/").filter(Boolean).at(-1) ?? path;
}

export function loadPermissionMode(): PermissionMode {
  const stored = localStorage.getItem("mycode-permission-mode");
  return stored === "read-only" ? "read-only" : "standard";
}

export default function App(): React.JSX.Element {
  const rpc = useMemo(() => new RpcClient(), []);
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>("idle");
  const connected = connectionStatus === "connected";
  const [connectionError, setConnectionError] = useState("");
  const [fatalError, setFatalError] = useState("");
  const [notice, setNotice] = useState<{ tone: "error" | "success"; message: string }>();
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
    loadPermissionMode,
  );
  const [fullAccessWarning, setFullAccessWarning] = useState(false);
  const [diff, setDiff] = useState<SessionDiff>({ checkpointId: null, files: [], diff: "" });
  const [sidebarMode, setSidebarMode] = useState<SidebarMode>("sessions");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("activity");
  const [rootFiles, setRootFiles] = useState<FileEntry[]>([]);
  const [openFiles, setOpenFiles] = useState<OpenFile[]>([]);
  const [activeFile, setActiveFile] = useState<string>();
  const [activeLine, setActiveLine] = useState<number>();
  const [codeSelection, setCodeSelection] = useState<CodeSelection>();
  const [attachments, setAttachments] = useState<PromptAttachment[]>([]);
  const [workspaceRevision, setWorkspaceRevision] = useState(0);
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
  const [undoing, setUndoing] = useState(false);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [sessionFilter, setSessionFilter] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const sessionIdRef = useRef<string | undefined>(undefined);
  const openFilesRef = useRef<OpenFile[]>([]);
  const notificationHandlerRef = useRef<(notification: RpcNotification) => void>(() => undefined);
  const reconnectRef = useRef<() => void>(() => undefined);

  useEffect(() => { sessionIdRef.current = sessionId; }, [sessionId]);
  useEffect(() => { openFilesRef.current = openFiles; }, [openFiles]);

  const reportError = useCallback((error: unknown, fallback: string) => {
    const detail = error instanceof Error ? error.message : String(error || fallback);
    setNotice({ tone: "error", message: detail || fallback });
  }, []);

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

  const loadDiff = useCallback(async (id?: string): Promise<SessionDiff> => {
    if (!id) {
      const empty = { checkpointId: null, files: [], diff: "" } satisfies SessionDiff;
      setDiff(empty);
      return empty;
    }
    const result = await rpc.request<SessionDiff>("session/diff", { sessionId: id });
    setDiff(result);
    return result;
  }, [rpc]);

  const refreshWorkspace = useCallback(async (changedPaths: string[] = []) => {
    const root = await rpc.request<{ entries: FileEntry[] }>("workspace/list", { path: "." });
    setRootFiles(root.entries);
    setWorkspaceRevision((value) => value + 1);
    if (openFilesRef.current.length === 0) return;
    const changed = new Set(changedPaths.map((path) => path.replaceAll("\\", "/")));
    const targets = changed.size
      ? openFilesRef.current.filter((file) => changed.has(file.path.replaceAll("\\", "/")))
      : openFilesRef.current;
    if (targets.length === 0) return;
    const refreshed = await Promise.all(targets.map(async (file) => {
      try { return await rpc.request<OpenFile>("workspace/read", { path: file.path }); }
      catch { return null; }
    }));
    const updates = new Map(refreshed.filter((file): file is OpenFile => file !== null).map((file) => [file.path, file]));
    const removed = new Set(targets.filter((_, index) => refreshed[index] === null).map((file) => file.path));
    setOpenFiles((items) => items.filter((file) => !removed.has(file.path)).map((file) => updates.get(file.path) ?? file));
    setActiveFile((path) => path && removed.has(path) ? undefined : path);
  }, [rpc]);

  useEffect(() => {
    let active = true;
    let connectedOnce = false;
    let reconnectTimer: number | undefined;
    let reconnectAttempt = 0;
    let connecting = false;
    const unsubscribe = rpc.onNotification((notification) => {
      if (!active) return;
      notificationHandlerRef.current(notification);
    });
    const unsubscribeConnection = rpc.onConnectionState((update) => {
      if (!active) return;
      setConnectionStatus(update.status);
      if (update.status === "connected") setConnectionError("");
      if (update.status === "disconnected") {
        setConnectionError(update.reason || "MyCode Web 连接已断开");
        scheduleReconnect();
      }
    });

    function scheduleReconnect(): void {
      if (!active || !connectedOnce || reconnectTimer !== undefined) return;
      const delay = Math.min(8000, 750 * (2 ** reconnectAttempt));
      reconnectAttempt += 1;
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = undefined;
        void connectAndLoad();
      }, delay);
    }

    async function connectAndLoad(): Promise<void> {
      if (!active || connecting) return;
      connecting = true;
      try {
        await rpc.connect();
        const initialized = await rpc.request<InitializeResult>("initialize", {});
        if (!active) return;
        setWorkspace(initialized.workspace);
        setModel(initialized.model);
        setProvider(initialized.provider);
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
        const preferred = sessionResult.sessions.find((item) => item.id === sessionIdRef.current) ?? sessionResult.sessions[0];
        const sessionResultPayload = preferred
          ? await rpc.request<SessionResult>("session/open", { sessionId: preferred.id })
          : await rpc.request<SessionResult>("session/new");
        setSessionId(sessionResultPayload.session.id);
        sessionIdRef.current = sessionResultPayload.session.id;
        dispatch({ type: "load", messages: sessionResultPayload.session.messages ?? [] });
        await loadDiff(sessionResultPayload.session.id);
        connectedOnce = true;
        reconnectAttempt = 0;
        setFatalError("");
        setConnectionError("");
      } catch (error) {
        if (!active) return;
        const message = error instanceof Error ? error.message : String(error);
        setConnectionError(message);
        if (!connectedOnce) setFatalError(message);
        else scheduleReconnect();
      } finally {
        connecting = false;
      }
    }

    reconnectRef.current = () => {
      setFatalError("");
      setConnectionError("");
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
      reconnectTimer = undefined;
      void connectAndLoad();
    };
    void connectAndLoad();
    return () => {
      active = false;
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
      unsubscribe();
      unsubscribeConnection();
      rpc.close();
    };
  }, [loadDiff, rpc]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("mycode-theme", theme);
  }, [theme]);

  useEffect(() => {
    localStorage.setItem("mycode-collaboration-mode", collaborationMode);
  }, [collaborationMode]);

  useEffect(() => {
    if (permissionMode === "full-access") {
      localStorage.removeItem("mycode-permission-mode");
    } else {
      localStorage.setItem("mycode-permission-mode", permissionMode);
    }
  }, [permissionMode]);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [run.messages, run.stream]);

  useEffect(() => {
    if (!modelMenuOpen) return;
    const closeMenu = (event: MouseEvent | KeyboardEvent): void => {
      if (event instanceof KeyboardEvent && event.key !== "Escape") return;
      if (event instanceof MouseEvent && event.target instanceof Element && event.target.closest(".model-switcher")) return;
      setModelMenuOpen(false);
    };
    document.addEventListener("mousedown", closeMenu);
    document.addEventListener("keydown", closeMenu);
    return () => {
      document.removeEventListener("mousedown", closeMenu);
      document.removeEventListener("keydown", closeMenu);
    };
  }, [modelMenuOpen]);

  function handleNotification(notification: RpcNotification): void {
    if (notification.method === "agent/event") {
      const event = notification.params as unknown as AgentEvent;
      dispatch({ type: "event", event });
      if (event.type === "tool.call.started" || event.type === "model.call.error") {
        setInspectorTab("activity");
        setInspectorOpen(true);
      }
      return;
    }
    if (notification.method === "permission/request") {
      const request = notification.params as unknown as PermissionRequest;
      setPermission(request);
      return;
    }
    if (notification.method === "run/result") {
      const result = notification.params;
      const status = String(result.status ?? "completed");
      const error = result.error ? String(result.error) : undefined;
      dispatch({
        type: "result",
        status,
        error,
        finalText: result.final_text ? String(result.final_text) : undefined,
      });
      setRunId(undefined);
      setPermission(undefined);
      const id = String(result.sessionId ?? sessionIdRef.current ?? "");
      if (id) {
        setSessionId(id);
        sessionIdRef.current = id;
      }
      if (error) setNotice({ tone: "error", message: error });
      void (async () => {
        try {
          const [, nextDiff] = await Promise.all([refreshSessions(), loadDiff(id)]);
          await refreshWorkspace(nextDiff.files.map((file) => file.path));
          if (nextDiff.files.length > 0) {
            setInspectorTab("diff");
            setInspectorOpen(true);
          }
        } catch (refreshError) {
          reportError(refreshError, "刷新运行结果失败");
        }
      })();
    }
  }

  notificationHandlerRef.current = handleNotification;

  async function newSession(): Promise<void> {
    if (run.status === "running") return;
    try {
      const result = await rpc.request<SessionResult>("session/new");
      setSessionId(result.session.id);
      sessionIdRef.current = result.session.id;
      dispatch({ type: "load", messages: result.session.messages ?? [] });
      setActiveFile(undefined);
      setAttachments([]);
      setDiff({ checkpointId: null, files: [], diff: "" });
      setPermissionMode("standard");
      await refreshSessions();
    } catch (error) {
      reportError(error, "新建会话失败");
    }
  }

  async function openSession(id: string): Promise<void> {
    if (run.status === "running") {
      setNotice({ tone: "error", message: "请先停止当前运行，再切换会话" });
      return;
    }
    try {
      const result = await rpc.request<SessionResult>("session/open", { sessionId: id });
      setSessionId(id);
      sessionIdRef.current = id;
      dispatch({ type: "load", messages: result.session.messages ?? [] });
      setActiveFile(undefined);
      setAttachments([]);
      setPermissionMode("standard");
      await loadDiff(id);
      setSidebarOpen(false);
    } catch (error) {
      reportError(error, "打开会话失败");
    }
  }

  async function deleteSession(id: string): Promise<void> {
    try {
      await rpc.request("session/delete", { sessionId: id });
      setPendingDelete(undefined);
      if (sessionIdRef.current === id) await newSession();
      else await refreshSessions();
    } catch (error) {
      reportError(error, "删除会话失败");
    }
  }

  async function startPrompt(prompt: string, mode: CollaborationMode): Promise<void> {
    if (!prompt || run.status === "running") return;
    const previousMessages = run.messages;
    const displayPrompt = attachments.length
      ? `${prompt}\n\n附件：${attachments.map((item) => item.label).join("、")}`
      : prompt;
    dispatch({ type: "user", content: displayPrompt });
    setActiveFile(undefined);
    const promptWithContext = attachments.length
      ? `${prompt}\n\n# 用户附加上下文\n${attachments.map((item) => item.promptText).join("\n\n")}`
      : prompt;
    try {
      const result = await rpc.request<{ runId: string; sessionId: string }>("run/start", {
        prompt: promptWithContext,
        collaborationMode: mode,
        permissionMode,
        ...(sessionIdRef.current ? { sessionId: sessionIdRef.current } : {}),
      });
      setRunId(result.runId);
      setSessionId(result.sessionId);
      sessionIdRef.current = result.sessionId;
      setDraft("");
      setAttachments([]);
      setInspectorTab("activity");
    } catch (error) {
      dispatch({ type: "load", messages: previousMessages });
      reportError(error, "启动运行失败");
    }
  }

  async function submit(): Promise<void> {
    const prompt = draft.trim();
    if (!prompt) return;
    await startPrompt(prompt, collaborationMode);
  }

  async function executePlan(): Promise<void> {
    if (!run.plan || run.status === "running") return;
    setCollaborationMode("default");
    await startPrompt("请按照上面已经确认的计划执行。完成修改后运行必要的验证，并总结结果。", "default");
  }

  async function cancelRun(): Promise<void> {
    try {
      await rpc.request("run/cancel", runId ? { runId } : {});
    } catch (error) {
      reportError(error, "取消运行失败");
    }
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
      reportError(error, "压缩上下文失败");
    } finally {
      setCompacting(false);
    }
  }

  async function decidePermission(approved: boolean, scope: "once" | "run" = "once"): Promise<void> {
    if (!permission) return;
    try {
      await rpc.request("permission/respond", { approvalId: permission.approvalId, approved, scope });
      setPermission(undefined);
    } catch (error) {
      reportError(error, "提交审批结果失败");
    }
  }

  function choosePermissionMode(next: PermissionMode): void {
    if (next === "full-access" && permissionMode !== "full-access") {
      setFullAccessWarning(true);
      return;
    }
    setPermissionMode(next);
  }

  async function openFile(path: string, line?: number): Promise<void> {
    const existing = openFiles.find((file) => file.path === path);
    if (existing) {
      setActiveFile(path);
      setActiveLine(line);
      setCodeSelection(undefined);
      setSidebarOpen(false);
      return;
    }
    try {
      const file = await rpc.request<OpenFile>("workspace/read", { path });
      setOpenFiles((items) => [...items, file]);
      setActiveFile(path);
      setActiveLine(line);
      setCodeSelection(undefined);
      setSidebarOpen(false);
    } catch (error) {
      reportError(error, `无法打开 ${path}`);
    }
  }

  function closeFile(path: string): void {
    setOpenFiles((items) => items.filter((file) => file.path !== path));
    if (activeFile === path) {
      setActiveFile(undefined);
      setActiveLine(undefined);
      setCodeSelection(undefined);
    }
  }

  async function searchWorkspace(event: FormEvent): Promise<void> {
    event.preventDefault();
    if (!searchQuery.trim()) return;
    try {
      const result = await rpc.request<{ matches: Array<{ path: string; line: number; preview: string }> }>("workspace/search", { query: searchQuery.trim(), limit: 200 });
      setSearchResults(result.matches);
    } catch (error) {
      reportError(error, "搜索项目失败");
    }
  }

  async function manualRefreshWorkspace(): Promise<void> {
    try {
      await refreshWorkspace();
      setNotice({ tone: "success", message: "文件列表已刷新" });
    } catch (error) {
      reportError(error, "刷新文件列表失败");
    }
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
        maxTokens: "",
        temperature: "",
        topP: "",
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
        maxTokens: preset.maxTokens == null ? "" : String(preset.maxTokens),
        temperature: preset.temperature == null ? "" : String(preset.temperature),
        topP: preset.topP == null ? "" : String(preset.topP),
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

  function editModel(profile: ModelProfile): void {
    const catalog = catalogs.find((item) => item.provider === profile.provider
      && item.models.some((modelOption) => modelOption.id === profile.model));
    setModelForm({
      catalogId: catalog?.id ?? "",
      name: profile.name,
      provider: profile.provider,
      model: profile.model,
      baseUrl: profile.baseUrl ?? catalog?.baseUrl ?? "",
      apiKeyEnv: profile.apiKeyEnv ?? catalog?.apiKeyEnv ?? "",
      apiKey: "",
      thinking: profile.thinking ?? "",
      thinkingFormat: profile.thinkingFormat ?? catalog?.thinkingFormat ?? "",
      thinkingBudget: profile.thinkingBudget == null ? "" : String(profile.thinkingBudget),
      reasoningEffort: profile.reasoningEffort ?? "",
      maxTokens: profile.maxTokens == null ? "" : String(profile.maxTokens),
      temperature: profile.temperature == null ? "" : String(profile.temperature),
      topP: profile.topP == null ? "" : String(profile.topP),
    });
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
          maxTokens: modelForm.maxTokens === "" ? null : Number(modelForm.maxTokens),
          temperature: modelForm.temperature === "" ? null : Number(modelForm.temperature),
          topP: modelForm.topP === "" ? null : Number(modelForm.topP),
        },
        ...(modelForm.apiKey ? { apiKey: modelForm.apiKey } : {}),
        replace: true,
      });
      setModelForm((value) => ({ ...value, apiKey: "" }));
      await refreshModels();
      setNotice({ tone: "success", message: `模型配置 ${modelForm.name} 已保存` });
    } catch (error) {
      reportError(error, "保存模型配置失败");
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
      sessionIdRef.current = result.session.id;
      dispatch({ type: "load", messages: result.session.messages ?? [] });
      setPermissionMode("standard");
      setSettingsOpen(false);
      setModelMenuOpen(false);
      setDiff({ checkpointId: null, files: [], diff: "" });
      await Promise.all([refreshModels(), refreshSessions()]);
      setNotice({ tone: "success", message: `已切换到 ${result.runtime.model}` });
    } catch (error) {
      reportError(error, "切换模型失败");
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
      setNotice({ tone: "success", message: `已删除模型配置 ${name}` });
    } catch (error) {
      reportError(error, "删除模型配置失败");
    } finally {
      setBusy(false);
    }
  }

  async function undoCheckpoint(): Promise<void> {
    if (!sessionIdRef.current || run.status === "running" || undoing) return;
    setUndoing(true);
    try {
      const result = await rpc.request<{ undone: boolean; files: string[]; summary: string }>("session/undo", { sessionId: sessionIdRef.current });
      await Promise.all([loadDiff(sessionIdRef.current), refreshWorkspace(result.files)]);
      setNotice({ tone: result.undone ? "success" : "error", message: result.summary });
    } catch (error) {
      reportError(error, "撤销文件修改失败");
    } finally {
      setUndoing(false);
    }
  }

  function attachCurrentFile(): void {
    if (!selectedFile) return;
    const id = `file:${selectedFile.path}`;
    setAttachments((items) => items.some((item) => item.id === id)
      ? items
      : [...items, { id, label: selectedFile.path, promptText: `@${selectedFile.path}` }]);
    setActiveFile(undefined);
  }

  function attachSelection(): void {
    if (!codeSelection) return;
    const id = `selection:${codeSelection.path}:${codeSelection.startLine}:${codeSelection.endLine}`;
    const text = codeSelection.text.slice(0, 12_000);
    setAttachments((items) => items.some((item) => item.id === id)
      ? items
      : [...items, {
        id,
        label: `${codeSelection.path}:L${codeSelection.startLine}-${codeSelection.endLine}`,
        promptText: `## ${codeSelection.path}:L${codeSelection.startLine}-${codeSelection.endLine}\n\`\`\`\n${text}\n\`\`\``,
      }]);
    setActiveFile(undefined);
  }

  const selectedFile = openFiles.find((file) => file.path === activeFile);
  const visibleSessions = sessions.filter((session) => {
    const needle = sessionFilter.trim().toLowerCase();
    return !needle || `${session.preview ?? ""} ${session.model}`.toLowerCase().includes(needle);
  });
  const cost = run.usage.cost === undefined ? "--" : `$${run.usage.cost.toFixed(6)}`;

  if (fatalError && !connected) return <main className="fatal-screen"><Bot size={34} /><h1>无法连接 MyCode Web</h1><p>{fatalError}</p><div className="fatal-actions"><button className="primary" onClick={() => reconnectRef.current()}><RefreshCw size={15} />重新连接</button></div><p>如果服务已经退出，请在终端重新运行 <code>mycode web</code>。</p></main>;

  return <main className={`app-shell ${sidebarOpen ? "sidebar-open" : ""} ${inspectorOpen ? "inspector-open" : ""}`}>
    <header className="topbar">
      <button className="icon-button mobile-menu" onClick={() => setSidebarOpen(!sidebarOpen)} title="打开导航"><Menu size={18} /></button>
      <div className="brand-badge"><span className="brand-mark"><Code2 size={17} /></span><span className="brand-wordmark">MyCode</span></div>
      <div className="workspace-name"><span>{basename(workspace)}</span><small>{workspace}</small></div>
      <div className="model-switcher">
        <button className="model-pill" onClick={() => setModelMenuOpen((value) => !value)} aria-expanded={modelMenuOpen}><Bot size={15} /><span>{model}</span><ChevronDown size={13} /></button>
        {modelMenuOpen && <div className="model-menu">
          <header><strong>切换模型</strong><button onClick={() => { setModelMenuOpen(false); setSettingsOpen(true); }}>管理配置</button></header>
          {models.length === 0 ? <span className="menu-empty">还没有已保存配置</span> : models.map((profile) => <button className={profile.active ? "active" : ""} disabled={profile.active || busy || run.status === "running"} key={profile.name} onClick={() => void useModel(profile.name)}><span><strong>{profile.name}</strong><small>{profile.model}</small></span>{profile.active && <Check size={14} />}</button>)}
        </div>}
      </div>
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

    {connectionError && <div className="connection-banner"><XCircle size={15} /><span>{connectionError}</span><button onClick={() => reconnectRef.current()}><RefreshCw size={13} />重连</button></div>}
    {notice && <div className={`notice-banner notice-${notice.tone}`} role="status"><span>{notice.message}</span><button onClick={() => setNotice(undefined)} aria-label="关闭通知"><X size={14} /></button></div>}

    <nav className="nav-rail" aria-label="主导航">
      <button className={sidebarMode === "sessions" ? "active" : ""} onClick={() => { setSidebarMode("sessions"); setSidebarOpen(true); }} title="会话"><MessageSquare size={19} /></button>
      <button className={sidebarMode === "files" ? "active" : ""} onClick={() => { setSidebarMode("files"); setSidebarOpen(true); }} title="文件"><Files size={19} /></button>
      <button className={sidebarMode === "search" ? "active" : ""} onClick={() => { setSidebarMode("search"); setSidebarOpen(true); }} title="搜索"><FileSearch size={19} /></button>
      <span className="rail-spacer" />
      <button onClick={() => setSettingsOpen(true)} title="设置"><Settings size={19} /></button>
    </nav>

    <aside className="sidebar">
      <div className="panel-heading"><strong>{sidebarMode === "sessions" ? "会话" : sidebarMode === "files" ? "文件" : "搜索"}</strong><span className="panel-heading-actions">{sidebarMode === "files" && <button className="icon-button" onClick={() => void manualRefreshWorkspace()} title="刷新文件"><RefreshCw size={14} /></button>}<button className="icon-button close-panel" onClick={() => setSidebarOpen(false)} aria-label="关闭导航"><X size={16} /></button></span></div>
      {sidebarMode === "sessions" && <>
        <button className="new-task" onClick={() => void newSession()} disabled={run.status === "running"}><Plus size={16} />新建任务</button>
        <label className="session-filter"><Search size={14} /><input value={sessionFilter} onChange={(event) => setSessionFilter(event.target.value)} placeholder="筛选会话" /></label>
        <div className="session-list">
          {visibleSessions.length === 0 && <div className="panel-empty">没有匹配的会话</div>}
          {visibleSessions.map((session) => <div className={`session-row ${session.id === sessionId ? "selected" : ""}`} key={session.id}>
            <button disabled={run.status === "running"} onClick={() => void openSession(session.id)}><strong>{session.preview || "新会话"}</strong><span>{session.turns ?? 0} 轮 · {session.model}</span></button>
            <button className="row-action" disabled={run.status === "running"} onClick={() => setPendingDelete(session.id)} title="删除会话"><Trash2 size={14} /></button>
            {pendingDelete === session.id && <div className="inline-confirm"><span>确认删除？</span><button onClick={() => setPendingDelete(undefined)}>取消</button><button className="danger" onClick={() => void deleteSession(session.id)}>删除</button></div>}
          </div>)}
        </div>
      </>}
      {sidebarMode === "files" && <FileTree key={workspaceRevision} entries={rootFiles} load={loadDirectory} open={(path) => void openFile(path)} activePath={activeFile} onError={(error) => reportError(error, "加载目录失败")} />}
      {sidebarMode === "search" && <>
        <form className="search-form" onSubmit={(event) => void searchWorkspace(event)}><Search size={15} /><input value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} placeholder="搜索项目内容" /><button type="submit">搜索</button></form>
        <div className="search-results">{searchQuery && searchResults.length === 0 && <div className="panel-empty">没有匹配结果</div>}{searchResults.map((result) => <button key={`${result.path}:${result.line}`} onClick={() => void openFile(result.path, result.line)}><strong>{result.path}:{result.line}</strong><span>{result.preview}</span></button>)}</div>
      </>}
    </aside>

    <section className="workspace">
      <div className="document-tabs">
        <button className={!activeFile ? "active" : ""} onClick={() => setActiveFile(undefined)}><MessageSquare size={14} />对话</button>
        {openFiles.map((file) => <div className={`document-tab ${activeFile === file.path ? "active" : ""}`} key={file.path}><button className="tab-target" onClick={() => { setActiveFile(file.path); setActiveLine(undefined); setCodeSelection(undefined); }}><FileCodeIcon /><span>{basename(file.path)}</span></button><button className="tab-close" onClick={() => closeFile(file.path)} aria-label={`关闭 ${file.path}`}><X size={12} /></button></div>)}
      </div>
      {selectedFile ? <div className="file-surface"><div className="file-meta"><span>{selectedFile.path}</span><span className="file-meta-actions"><small>{selectedFile.lines} 行 · 只读</small><button onClick={attachCurrentFile}><Paperclip size={13} />附加文件</button><button disabled={!codeSelection} onClick={attachSelection}><Paperclip size={13} />附加选区</button></span></div><Suspense fallback={<div className="code-loading"><LoaderCircle className="spin" size={18} />正在加载代码预览</div>}><CodeViewer file={selectedFile} focusLine={activeLine} onSelection={setCodeSelection} /></Suspense></div> : <>
        <div className="conversation" aria-live="polite">
          {run.messages.filter((message) => (message.role === "user" || message.role === "assistant") && message.content).length === 0 && <div className="empty-state"><Bot size={30} /><h2>开始一个编码任务</h2><p>描述目标，或从一个常见工作流开始。</p><div className="starter-actions"><button onClick={() => setDraft("检查当前项目并说明最值得优先修复的问题")}>检查项目</button><button onClick={() => setDraft("运行测试，定位失败原因并修复")}>修复测试</button><button onClick={() => { setCollaborationMode("plan"); setDraft("分析这个项目并制定下一步实施计划"); }}>制定计划</button></div></div>}
          {run.messages.map((message, index) => (message.role === "user" || message.role === "assistant") && message.content ? <article className={`message message-${message.role}`} key={index}><header>{message.role === "user" ? "你" : "MyCode"}</header><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>{message.content}</ReactMarkdown></article> : null)}
          {run.stream && <article className="message message-assistant streaming"><header>MyCode <LoaderCircle size={13} className="spin" /></header><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>{run.stream}</ReactMarkdown></article>}
          {run.error && <div className="inline-error"><XCircle size={16} /><div><strong>运行未完成</strong><span>{run.error}</span></div><button onClick={() => { setInspectorTab("activity"); setInspectorOpen(true); }} aria-label="查看运行详情">查看详情</button></div>}
          {run.plan && run.status !== "running" && collaborationMode === "plan" && <div className="plan-actions"><span>计划已生成。确认后可在当前会话中继续执行。</span><button className="primary" onClick={() => void executePlan()}><Play size={14} />执行此计划</button></div>}
          <div ref={bottomRef} />
        </div>
        <div className="composer-wrap">
          {attachments.length > 0 && <div className="attachment-strip">{attachments.map((item) => <span className="attachment-chip" key={item.id}><Paperclip size={12} /><span>{item.label}</span><button onClick={() => setAttachments((items) => items.filter((candidate) => candidate.id !== item.id))} aria-label={`移除 ${item.label}`}><X size={12} /></button></span>)}</div>}
          <div className="composer">
            <textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); void submit(); } }} placeholder={collaborationMode === "plan" ? "描述需要规划的任务…" : collaborationMode === "review" ? "描述需要审查的范围…" : "描述你想完成的任务…"} rows={3} />
            <button disabled={!connected || (run.status !== "running" && !draft.trim())} className={`send-button ${run.status === "running" ? "stop" : ""}`} onClick={() => run.status === "running" ? void cancelRun() : void submit()} title={run.status === "running" ? "停止运行" : "发送"}>{run.status === "running" ? <CircleStop size={18} /> : <Send size={18} />}</button>
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
      <RunInspector
        tab={inspectorTab}
        onTabChange={setInspectorTab}
        onClose={() => setInspectorOpen(false)}
        activities={run.activities}
        diff={diff}
        context={run.context}
        compacted={run.compacted}
        compacting={compacting}
        undoing={undoing}
        runActive={run.status === "running"}
        planProgress={run.planProgress}
        onCompact={() => void compactContext()}
        onUndo={() => void undoCheckpoint()}
        onOpenFile={(path, line) => void openFile(path, line)}
      />
    </aside>

    {(sidebarOpen || inspectorOpen) && <button className="panel-overlay" onClick={() => { setSidebarOpen(false); setInspectorOpen(false); }} aria-label="关闭面板" />}

    <footer className="statusbar"><span>{provider}</span><span>{collaborationMode === "default" ? "执行" : collaborationMode === "plan" ? "计划" : "审查"}</span><span>{run.usage.input} 输入 / {run.usage.output} 输出</span>{run.usage.cached > 0 && <span>{run.usage.cached} 缓存</span>}<span>{cost}</span>{run.capacity && (
      <span className="capacity-bar" title={`上下文: ${run.capacity.used} / ${run.capacity.limit} tokens (${run.capacity.percent}%)`}>
        <span
          className={`capacity-fill ${run.capacity.percent >= 70 ? "capacity-high" : run.capacity.percent >= 50 ? "capacity-medium" : ""}`}
          style={{ width: `${run.capacity.percent}%` }}
        />
        <span className="capacity-label">{run.capacity.percent}%</span>
      </span>
    )}<span className="status-spacer" /><span className={`statusbar-pill ${connected ? "connected" : "connecting"}`}>{connected ? "仅本机 · 已连接" : connectionStatus === "connecting" ? "正在重连" : "已断开"}</span></footer>

    {permission && <ApprovalDialog request={permission} onDecision={(approved, scope) => void decidePermission(approved, scope)} />}

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
      onEdit={editModel}
      onDeleteRequest={setPendingDelete}
      onRemove={(name) => void removeModel(name)}
    />}
  </main>;
}

function FileCodeIcon(): React.JSX.Element {
  return <Code2 size={14} />;
}

function ApprovalDialog({ request, onDecision }: { request: PermissionRequest; onDecision: (approved: boolean, scope?: "once" | "run") => void }): React.JSX.Element {
  const isWrite = request.kind === "write";
  const dialogRef = useRef<HTMLElement>(null);
  const onDecisionRef = useRef(onDecision);
  onDecisionRef.current = onDecision;
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : undefined;
    dialogRef.current?.querySelector<HTMLElement>("button")?.focus();
    const handleKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") onDecisionRef.current(false);
    };
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("keydown", handleKey);
      previous?.focus();
    };
  }, []);
  return <div className="approval-backdrop" role="presentation">
    <section ref={dialogRef} className="approval-dialog" role="alertdialog" aria-modal="true" aria-label="操作确认">
      <header>
        <span className="approval-icon">{isWrite ? <FileDiff size={18} /> : <ActivityIcon size={18} />}</span>
        <div><h2>{isWrite ? "确认文件修改" : "确认执行命令"}</h2><p>{request.prompt}</p></div>
        <span className="approval-risk">{request.risk || request.kind}</span>
      </header>
      {request.display_path && <div className="approval-path">{request.display_path}</div>}
      {request.command && <pre className="approval-content command-content">{request.command}</pre>}
      {request.diff && <pre className="approval-content diff-content">{request.diff}</pre>}
      <footer><span>审批仅作用于当前运行，不会永久保存</span><div><button onClick={() => onDecision(false)}>拒绝</button>{request.canRememberForRun && <button onClick={() => onDecision(true, "run")}>本轮允许此操作</button>}<button className="primary" onClick={() => onDecision(true, "once")}>允许本次</button></div></footer>
    </section>
  </div>;
}
