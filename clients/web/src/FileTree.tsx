import { ChevronDown, ChevronRight, FileCode2, Folder, FolderOpen } from "lucide-react";
import { useState } from "react";
import { FileEntry } from "./protocol";

interface FileTreeProps {
  entries: FileEntry[];
  load(path: string): Promise<FileEntry[]>;
  open(path: string): void;
  activePath?: string;
}

function Node({ entry, load, open, activePath }: { entry: FileEntry } & Omit<FileTreeProps, "entries">): React.JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const [children, setChildren] = useState<FileEntry[]>();
  async function toggle(): Promise<void> {
    if (entry.type === "file") {
      open(entry.path);
      return;
    }
    const next = !expanded;
    setExpanded(next);
    if (next && children === undefined) setChildren(await load(entry.path));
  }
  return <div className="tree-node">
    <button className={`tree-row ${activePath === entry.path ? "selected" : ""} ${entry.type === "directory" ? (expanded ? "directory expanded" : "directory") : "file"}`} onClick={() => void toggle()} title={entry.path}>
      {entry.type === "directory" ? expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} /> : <span className="tree-spacer" />}
      {entry.type === "directory" ? expanded ? <FolderOpen size={15} /> : <Folder size={15} /> : <FileCode2 size={15} />}
      <span>{entry.name}</span>
    </button>
    {expanded && children && <div className="tree-children">
      {children.map((child) => <Node key={child.path} entry={child} load={load} open={open} activePath={activePath} />)}
    </div>}
  </div>;
}

export function FileTree(props: FileTreeProps): React.JSX.Element {
  return <div className="file-tree">
    {props.entries.map((entry) => <Node key={entry.path} entry={entry} load={props.load} open={props.open} activePath={props.activePath} />)}
  </div>;
}
