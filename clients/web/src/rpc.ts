import { isNotification, RpcNotification, RpcRequest, RpcResponse } from "./protocol";

type Pending = { resolve(value: unknown): void; reject(error: Error): void };

export class RpcClient {
  private socket?: WebSocket;
  private sequence = 0;
  private pending = new Map<number, Pending>();
  private listeners = new Set<(message: RpcNotification) => void>();

  async connect(): Promise<void> {
    const fragment = new URLSearchParams(window.location.hash.slice(1));
    const token = fragment.get("token") ?? "";
    history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${scheme}://${window.location.host}/ws`);
    this.socket = socket;
    await new Promise<void>((resolve, reject) => {
      const timeout = window.setTimeout(() => reject(new Error("连接认证超时")), 7000);
      socket.addEventListener("open", () => socket.send(JSON.stringify({ type: "auth", token })), { once: true });
      socket.addEventListener("message", (event) => {
        try {
          const message = JSON.parse(String(event.data)) as Record<string, unknown>;
          if (message.type === "authenticated") {
            window.clearTimeout(timeout);
            resolve();
          }
        } catch (error) {
          reject(error);
        }
      }, { once: true });
      socket.addEventListener("close", (event) => {
        window.clearTimeout(timeout);
        reject(new Error(event.reason || "连接已关闭"));
      }, { once: true });
    });
    socket.addEventListener("message", (event) => this.handleMessage(String(event.data)));
    socket.addEventListener("close", () => this.failAll(new Error("MyCode Web 连接已断开")));
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

  close(): void {
    this.socket?.close();
  }

  private handleMessage(raw: string): void {
    const message = JSON.parse(raw) as RpcResponse | RpcNotification;
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
}
