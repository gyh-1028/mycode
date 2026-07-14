import { isNotification, RpcNotification, RpcRequest, RpcResponse } from "./protocol";

type Pending = { resolve(value: unknown): void; reject(error: Error): void };
export type ConnectionStatus = "idle" | "connecting" | "connected" | "disconnected";
export interface ConnectionUpdate { status: ConnectionStatus; reason?: string }

const TOKEN_KEY = "mycode-web-token";

export function resolveWebToken(): string {
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const fromFragment = fragment.get("token") ?? "";
  if (fromFragment) {
    try { sessionStorage.setItem(TOKEN_KEY, fromFragment); } catch { /* storage may be disabled */ }
    history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
    return fromFragment;
  }
  try { return sessionStorage.getItem(TOKEN_KEY) ?? ""; } catch { return ""; }
}

export class RpcClient {
  private socket?: WebSocket;
  private sequence = 0;
  private pending = new Map<number, Pending>();
  private listeners = new Set<(message: RpcNotification) => void>();
  private connectionListeners = new Set<(update: ConnectionUpdate) => void>();
  private connectTask?: Promise<void>;
  private status: ConnectionStatus = "idle";
  private manualClose = false;

  async connect(): Promise<void> {
    if (this.socket?.readyState === WebSocket.OPEN) return;
    if (this.connectTask) return this.connectTask;
    this.connectTask = this.openSocket().finally(() => { this.connectTask = undefined; });
    return this.connectTask;
  }

  private async openSocket(): Promise<void> {
    const token = resolveWebToken();
    if (!token) throw new Error("缺少 MyCode Web 临时认证令牌，请从终端重新打开工作台");
    this.manualClose = false;
    this.emitConnection({ status: "connecting" });
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${scheme}://${window.location.host}/ws`);
    this.socket = socket;
    try {
      await new Promise<void>((resolve, reject) => {
        const cleanup = (): void => {
          window.clearTimeout(timeout);
          socket.removeEventListener("message", onMessage);
          socket.removeEventListener("close", onClose);
          socket.removeEventListener("error", onError);
        };
        const fail = (error: Error): void => {
          cleanup();
          reject(error);
        };
        const onMessage = (event: MessageEvent): void => {
          try {
            const message = JSON.parse(String(event.data)) as Record<string, unknown>;
            if (message.type === "authenticated") {
              cleanup();
              resolve();
            }
          } catch (error) {
            fail(error instanceof Error ? error : new Error(String(error)));
          }
        };
        const onClose = (event: CloseEvent): void => fail(new Error(event.reason || "连接已关闭"));
        const onError = (): void => fail(new Error("无法连接 MyCode Web 服务"));
        const timeout = window.setTimeout(() => fail(new Error("连接认证超时")), 7000);
        socket.addEventListener("open", () => socket.send(JSON.stringify({ type: "auth", token })), { once: true });
        socket.addEventListener("message", onMessage);
        socket.addEventListener("close", onClose);
        socket.addEventListener("error", onError);
      });
    } catch (error) {
      if (this.socket === socket) this.socket = undefined;
      if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) socket.close();
      const reason = error instanceof Error ? error.message : String(error);
      this.emitConnection({ status: "disconnected", reason });
      throw error;
    }
    socket.addEventListener("message", (event) => this.handleMessage(String(event.data)));
    socket.addEventListener("close", (event) => {
      if (this.socket !== socket) return;
      const error = new Error(event.reason || "MyCode Web 连接已断开");
      this.failAll(error);
      this.socket = undefined;
      if (!this.manualClose) this.emitConnection({ status: "disconnected", reason: error.message });
    });
    this.emitConnection({ status: "connected" });
  }

  request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error("MyCode Web 尚未连接"));
    }
    const id = ++this.sequence;
    const message: RpcRequest = { jsonrpc: "2.0", id, method, params };
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve: (value) => resolve(value as T), reject });
      this.socket!.send(JSON.stringify(message));
    });
  }

  onNotification(listener: (message: RpcNotification) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  onConnectionState(listener: (update: ConnectionUpdate) => void): () => void {
    this.connectionListeners.add(listener);
    listener({ status: this.status });
    return () => this.connectionListeners.delete(listener);
  }

  close(): void {
    this.manualClose = true;
    this.socket?.close();
    this.socket = undefined;
    this.emitConnection({ status: "idle" });
  }

  private handleMessage(raw: string): void {
    let message: RpcResponse | RpcNotification;
    try {
      message = JSON.parse(raw) as RpcResponse | RpcNotification;
    } catch {
      this.socket?.close(1002, "invalid JSON-RPC response");
      return;
    }
    if (isNotification(message)) {
      this.listeners.forEach((listener) => listener(message));
      return;
    }
    const pending = this.pending.get(message.id ?? -1);
    if (!pending) return;
    this.pending.delete(message.id ?? -1);
    if (message.error) pending.reject(new Error(`${message.error.message} (${message.error.code})`));
    else pending.resolve(message.result);
  }

  private failAll(error: Error): void {
    this.pending.forEach((pending) => pending.reject(error));
    this.pending.clear();
  }

  private emitConnection(update: ConnectionUpdate): void {
    this.status = update.status;
    this.connectionListeners.forEach((listener) => listener(update));
  }
}
