import {
  Activity as ActivityIcon,
  Bot,
  Check,
  ChevronRight,
  CircleAlert,
  FileDiff,
  FolderSearch2,
  LoaderCircle,
  Minimize2,
  RotateCcw,
  Wrench,
  X,
  XCircle,
} from "lucide-react";
import { useEffect, useState } from "react";
import type { ContextSelection, DiffFile, SessionDiff } from "./protocol";
import type { Activity } from "./state";

export type InspectorTab = "activity" | "diff" | "context";

interface RunInspectorProps {
  tab: InspectorTab;
  onTabChange(tab: InspectorTab): void;
  onClose(): void;
  activities: Activity[];
  diff: SessionDiff;
  context?: ContextSelection;
  compacted?: boolean;
  compacting: boolean;
  undoing: boolean;
  runActive: boolean;
  planProgress?: string;
  onCompact(): void;
  onUndo(): void;
  onOpenFile(path: string, line?: number): void;
}

export function RunInspector({
  tab,
  onTabChange,
  onClose,
  activities,
  diff,
  context,
  compacted,
  compacting,
  undoing,
  runActive,
  planProgress,
  onCompact,
  onUndo,
  onOpenFile,
}: RunInspectorProps): React.JSX.Element {
  const [selectedPath, setSelectedPath] = useState<string>();
  const [confirmUndo, setConfirmUndo] = useState(false);

  useEffect(() => {
    if (!diff.files.some((file) => file.path === selectedPath)) setSelectedPath(diff.files[0]?.path);
    setConfirmUndo(false);
  }, [diff.checkpointId, diff.files, selectedPath]);

  const selectedDiff = diff.files.find((file) => file.path === selectedPath) ?? diff.files[0];

  return <>
    <div className="inspector-tabs" role="tablist" aria-label="运行检查器">
      <button role="tab" aria-selected={tab === "activity"} className={tab === "activity" ? "active" : ""} onClick={() => onTabChange("activity")}><ActivityIcon size={15} />活动{activities.length > 0 && <span>{activities.length}</span>}</button>
      <button role="tab" aria-selected={tab === "diff"} className={tab === "diff" ? "active" : ""} onClick={() => onTabChange("diff")}><FileDiff size={15} />Diff{diff.files.length > 0 && <span>{diff.files.length}</span>}</button>
      <button role="tab" aria-selected={tab === "context"} className={tab === "context" ? "active" : ""} onClick={() => onTabChange("context")}><FolderSearch2 size={15} />上下文{context?.items.length ? <span>{context.items.length}</span> : null}</button>
      <button className="icon-button close-panel" onClick={onClose} aria-label="关闭运行详情"><X size={16} /></button>
    </div>
    <div className="inspector-content">
      {tab === "activity" && <ActivityPanel activities={activities} planProgress={planProgress} />}
      {tab === "diff" && <div className="diff-panel">
        <div className="inspector-toolbar">
          <span>{diff.files.length ? `${diff.files.length} 个文件已修改` : "当前会话没有文件修改"}</span>
          {diff.checkpointId && !confirmUndo && <button className="secondary-action" disabled={runActive || undoing} onClick={() => setConfirmUndo(true)}><RotateCcw size={13} />撤销本轮</button>}
          {confirmUndo && <div className="toolbar-confirm"><span>确认还原这些文件？</span><button onClick={() => setConfirmUndo(false)}>取消</button><button className="danger" disabled={undoing} onClick={onUndo}>{undoing ? "撤销中…" : "确认"}</button></div>}
        </div>
        {diff.files.length > 0 && <div className="diff-file-tabs">
          {diff.files.map((file) => <button className={file.path === selectedDiff?.path ? "active" : ""} key={file.path} onClick={() => setSelectedPath(file.path)}><span className={`change-kind kind-${file.kind}`}>{file.kind === "created" ? "A" : "M"}</span><span>{file.path}</span></button>)}
        </div>}
        {selectedDiff ? <DiffView file={selectedDiff} onOpen={() => onOpenFile(selectedDiff.path)} /> : <div className="panel-empty">Agent 的文件修改会显示在这里</div>}
      </div>}
      {tab === "context" && <div className="context-panel">
        <div className="context-toolbar">
          <button className="compact-button" disabled={runActive || compacting} onClick={onCompact} title="手动压缩较早的对话上下文">
            {compacting ? <LoaderCircle size={13} className="spin" /> : <Minimize2 size={13} />}压缩上下文
          </button>
          {compacted && <span className="compacted-badge">已压缩</span>}
          {context && <span className="context-token-count">约 {context.estimated_tokens} tokens</span>}
        </div>
        {!context?.items.length && !context?.degraded.length && <div className="panel-empty">本轮尚未选择自动上下文</div>}
        {context?.degraded.map((reason, index) => <div className="context-warning" key={`${reason}-${index}`}><CircleAlert size={14} /><span>{reason}</span></div>)}
        <div className="context-items">
          {context?.items.map((item, index) => <button key={`${item.path}:${item.start_line}:${index}`} onClick={() => onOpenFile(item.path, item.start_line)}>
            <span className="context-path">{item.path}</span>
            <span className="context-range">L{item.start_line}–{item.end_line}</span>
            <strong>{item.symbol || item.reason}</strong>
            <small>{item.symbol ? item.reason : `相关度 ${item.score.toFixed(1)}`}</small>
          </button>)}
        </div>
      </div>}
    </div>
  </>;
}

function ActivityPanel({ activities, planProgress }: { activities: Activity[]; planProgress?: string }): React.JSX.Element {
  if (activities.length === 0 && !planProgress) return <div className="panel-empty">运行后可在这里检查模型与工具步骤</div>;
  return <div className="timeline">
    {planProgress && <div className="plan-progress"><Check size={14} /><span>{planProgress}</span></div>}
    {activities.map((activity) => <details className={`timeline-row ${activity.status}`} key={activity.id}>
      <summary>
        <span className="timeline-status">{activity.status === "running" ? <LoaderCircle size={15} className="spin" /> : activity.status === "error" ? <XCircle size={15} /> : <Check size={15} />}</span>
        <span className="timeline-kind">{activity.kind === "model" ? <Bot size={14} /> : <Wrench size={14} />}</span>
        <span className="timeline-main"><strong>{activity.name}</strong><small>{activity.summary}</small></span>
        {activity.duration !== undefined && <time>{activity.duration}ms</time>}
        <ChevronRight className="timeline-chevron" size={14} />
      </summary>
      <div className="timeline-detail"><pre>{activity.detail || activity.summary}</pre></div>
    </details>)}
  </div>;
}

function DiffView({ file, onOpen }: { file: DiffFile; onOpen(): void }): React.JSX.Element {
  return <div className="structured-diff">
    <div className="diff-header"><span>{file.path}</span><button onClick={onOpen}>打开文件</button></div>
    <pre>{file.diff.split("\n").map((line, index) => <span className={diffLineClass(line)} key={`${index}-${line.slice(0, 20)}`}>{line || " "}{"\n"}</span>)}</pre>
  </div>;
}

function diffLineClass(line: string): string {
  if (line.startsWith("+++ ") || line.startsWith("--- ")) return "diff-meta";
  if (line.startsWith("@@")) return "diff-hunk";
  if (line.startsWith("+")) return "diff-add";
  if (line.startsWith("-")) return "diff-delete";
  return "diff-context";
}
