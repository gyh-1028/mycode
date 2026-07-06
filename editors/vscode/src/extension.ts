import * as crypto from "node:crypto";
import * as path from "node:path";
import * as vscode from "vscode";
import { MyCodeClient } from "./client.js";
import { RpcNotification } from "./protocol.js";

interface SessionSummary {
  id: string;
  model: string;
  provider: string;
  updated_at?: string;
  turns?: number;
  preview?: string;
  messages?: Array<Record<string, unknown>>;
}

class SessionItem extends vscode.TreeItem {
  constructor(readonly session: SessionSummary) {
    super(session.preview || "New session", vscode.TreeItemCollapsibleState.None);
    this.description = `${session.turns ?? 0} turns`;
    this.tooltip = `${session.id}\n${session.model}`;
    this.iconPath = new vscode.ThemeIcon("comment-discussion");
    this.command = { command: "mycode.openSession", title: "Open Session", arguments: [session.id] };
  }
}

class SessionProvider implements vscode.TreeDataProvider<SessionItem> {
  private readonly changed = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this.changed.event;
  private sessions: SessionSummary[] = [];

  setSessions(sessions: SessionSummary[]): void {
    this.sessions = sessions;
    this.changed.fire();
  }

  refresh(): void { this.changed.fire(); }
  getTreeItem(item: SessionItem): vscode.TreeItem { return item; }
  getChildren(): SessionItem[] { return this.sessions.map((session) => new SessionItem(session)); }
}

class ChatViewProvider implements vscode.WebviewViewProvider, vscode.Disposable {
  private view: vscode.WebviewView | undefined;
  private client: MyCodeClient | undefined;
  private initialized: Record<string, unknown> | undefined;
  private readonly disposables: vscode.Disposable[] = [];

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly sessions: SessionProvider,
    private readonly output: vscode.OutputChannel,
  ) {}

  async resolveWebviewView(view: vscode.WebviewView): Promise<void> {
    this.view = view;
    view.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.context.extensionUri, "dist")],
    };
    view.webview.html = this.html(view.webview);
    this.disposables.push(view.webview.onDidReceiveMessage((message) => this.handleMessage(message)));
    this.disposables.push(view.onDidDispose(() => { this.view = undefined; }));
  }

  private async ensureClient(): Promise<MyCodeClient> {
    if (this.client) return this.client;
    const workspace = vscode.workspace.workspaceFolders?.[0];
    if (!workspace) throw new Error("Open a workspace folder before starting MyCode");
    const client = new MyCodeClient(workspace, this.output);
    this.initialized = await client.start();
    this.disposables.push(client.onNotification((event) => this.onNotification(event)));
    this.client = client;
    await this.refreshSessions();
    this.post({ type: "initialized", value: this.initialized });
    return client;
  }

  private async handleMessage(message: Record<string, unknown>): Promise<void> {
    try {
      const client = await this.ensureClient();
      switch (message.type) {
        case "ready":
          this.post({ type: "initialized", value: this.initialized });
          break;
        case "startRun": {
          const result = await client.request("run/start", {
            prompt: String(message.prompt ?? ""),
            ...(message.sessionId ? { sessionId: String(message.sessionId) } : {}),
          });
          this.post({ type: "runStarted", value: result });
          break;
        }
        case "cancelRun":
          await client.request("run/cancel", message.runId ? { runId: message.runId } : {});
          break;
        case "permissionResponse":
          await client.request("permission/respond", {
            approvalId: String(message.approvalId),
            approved: Boolean(message.approved),
          });
          break;
        case "newSession": {
          const result = await client.request("session/new") as { session: SessionSummary };
          this.post({ type: "session", value: result.session });
          await this.refreshSessions();
          break;
        }
        case "openSession":
          await this.openSession(String(message.sessionId));
          break;
      }
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      this.post({ type: "error", value: detail });
      void vscode.window.showErrorMessage(`MyCode: ${detail}`);
    }
  }

  private onNotification(event: RpcNotification): void {
    this.post({ type: "notification", value: event });
    if (event.method === "run/result") void this.refreshSessions();
  }

  async refreshSessions(): Promise<void> {
    if (!this.client) return;
    const result = await this.client.request("session/list") as { sessions: SessionSummary[] };
    this.sessions.setSessions(result.sessions);
  }

  async newSession(): Promise<void> {
    const client = await this.ensureClient();
    const result = await client.request("session/new") as { session: SessionSummary };
    this.post({ type: "session", value: result.session });
    await this.refreshSessions();
  }

  async openSession(sessionId: string): Promise<void> {
    const client = await this.ensureClient();
    const result = await client.request("session/open", { sessionId }) as { session: SessionSummary };
    this.post({ type: "session", value: result.session });
  }

  revealWithDraft(text?: string): void {
    void vscode.commands.executeCommand("mycode.chat.focus");
    if (text) this.post({ type: "draft", value: text });
  }

  private post(message: unknown): void { void this.view?.webview.postMessage(message); }

  private html(webview: vscode.Webview): string {
    const nonce = crypto.randomBytes(16).toString("base64");
    const script = webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, "dist", "webview.js"));
    const styles = webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, "dist", "webview.css"));
    const codicons = webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, "dist", "codicons", "codicon.css"));
    return `<!doctype html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource}; font-src ${webview.cspSource}; script-src 'nonce-${nonce}';"><link rel="stylesheet" href="${codicons}"><link rel="stylesheet" href="${styles}"><title>MyCode</title></head><body><div id="root"></div><script nonce="${nonce}" src="${script}"></script></body></html>`;
  }

  dispose(): void {
    void this.client?.dispose();
    this.disposables.forEach((item) => item.dispose());
  }
}

export function activate(context: vscode.ExtensionContext): void {
  const output = vscode.window.createOutputChannel("MyCode", { log: true });
  const sessions = new SessionProvider();
  const chat = new ChatViewProvider(context, sessions, output);
  context.subscriptions.push(
    output,
    chat,
    vscode.window.registerTreeDataProvider("mycode.sessions", sessions),
    vscode.window.registerWebviewViewProvider("mycode.chat", chat, { webviewOptions: { retainContextWhenHidden: true } }),
    vscode.commands.registerCommand("mycode.open", () => chat.revealWithDraft()),
    vscode.commands.registerCommand("mycode.newSession", () => chat.newSession()),
    vscode.commands.registerCommand("mycode.refreshSessions", () => chat.refreshSessions()),
    vscode.commands.registerCommand("mycode.openSession", (sessionId: string) => chat.openSession(sessionId)),
    vscode.commands.registerCommand("mycode.askSelection", () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor || editor.selection.isEmpty) return;
      const relative = vscode.workspace.asRelativePath(editor.document.uri);
      const selected = editor.document.getText(editor.selection);
      chat.revealWithDraft(`请查看 @${relative} 并处理以下选中内容：\n\n${selected}`);
    }),
  );
}

export function deactivate(): void {}
