import { ChildProcessWithoutNullStreams, spawn } from "node:child_process";
import { createInterface } from "node:readline";
import * as vscode from "vscode";
import { isNotification, parseProtocolLine, RpcNotification, RpcRequest, RpcResponse } from "./protocol.js";

interface PendingRequest {
  resolve(value: unknown): void;
  reject(error: Error): void;
}

export class MyCodeClient implements vscode.Disposable {
  private process: ChildProcessWithoutNullStreams | undefined;
  private sequence = 0;
  private pending = new Map<number, PendingRequest>();
  private readonly notificationEmitter = new vscode.EventEmitter<RpcNotification>();
  readonly onNotification = this.notificationEmitter.event;

  constructor(
    private readonly workspace: vscode.WorkspaceFolder,
    private readonly output: vscode.OutputChannel,
  ) {}

  async start(): Promise<Record<string, unknown>> {
    if (this.process) throw new Error("MyCode server is already running");
    const config = vscode.workspace.getConfiguration("mycode", this.workspace.uri);
    const executable = config.get<string>("executable", "mycode");
    const extraArgs = config.get<string[]>("extraArgs", []);
    this.process = spawn(executable, [...extraArgs, "serve", "--stdio"], {
      cwd: this.workspace.uri.fsPath,
      env: process.env,
      windowsHide: true,
      stdio: "pipe",
    });
    this.process.stderr.setEncoding("utf8");
    this.process.stderr.on("data", (chunk: string) => this.output.append(chunk));
    this.process.on("error", (error) => this.failAll(error));
    this.process.on("exit", (code, signal) => {
      this.failAll(new Error(`MyCode server exited (${code ?? signal ?? "unknown"})`));
      this.process = undefined;
    });
    const lines = createInterface({ input: this.process.stdout });
    lines.on("line", (line) => this.handleLine(line));
    return (await this.request("initialize", {
      workspace: this.workspace.uri.fsPath,
      client: { name: "mycode-vscode", version: "0.2.1" },
    })) as Record<string, unknown>;
  }

  request(method: string, params: Record<string, unknown> = {}): Promise<unknown> {
    if (!this.process) return Promise.reject(new Error("MyCode server is not running"));
    const id = ++this.sequence;
    const request: RpcRequest = { jsonrpc: "2.0", id, method, params };
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.process!.stdin.write(`${JSON.stringify(request)}\n`);
    });
  }

  notify(method: string, params: Record<string, unknown> = {}): void {
    this.process?.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", method, params })}\n`);
  }

  private handleLine(line: string): void {
    if (!line.trim()) return;
    let message: RpcResponse | RpcNotification;
    try {
      message = parseProtocolLine(line);
    } catch (error) {
      this.output.appendLine(`Invalid protocol line: ${line}`);
      this.failAll(error instanceof Error ? error : new Error(String(error)));
      return;
    }
    if (isNotification(message)) {
      this.notificationEmitter.fire(message);
      return;
    }
    const pending = this.pending.get(message.id ?? -1);
    if (!pending) return;
    this.pending.delete(message.id ?? -1);
    if (message.error) {
      pending.reject(new Error(`${message.error.message} (${message.error.code})`));
    } else {
      pending.resolve(message.result);
    }
  }

  private failAll(error: Error): void {
    for (const request of this.pending.values()) request.reject(error);
    this.pending.clear();
  }

  async dispose(): Promise<void> {
    if (!this.process) return;
    try {
      await this.request("shutdown");
    } catch {
      // Process may already be gone.
    }
    this.process.kill();
    this.process = undefined;
    this.notificationEmitter.dispose();
  }
}
