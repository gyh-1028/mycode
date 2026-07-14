import { basicSetup } from "codemirror";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { python } from "@codemirror/lang-python";
import { EditorState } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { useEffect, useRef } from "react";
import { CodeSelection, OpenFile } from "./protocol";

function languageExtension(language: string) {
  if (language === "python") return python();
  if (language === "javascript") return javascript({ jsx: true });
  if (language === "typescript") return javascript({ jsx: true, typescript: true });
  if (language === "json") return json();
  if (language === "markdown") return markdown();
  return [];
}

interface CodeViewerProps {
  file: OpenFile;
  focusLine?: number;
  onSelection?(selection?: CodeSelection): void;
}

export function CodeViewer({ file, focusLine, onSelection }: CodeViewerProps): React.JSX.Element {
  const host = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!host.current) return;
    const state = EditorState.create({
      doc: file.content,
      extensions: [
        basicSetup,
        languageExtension(file.language),
        EditorState.readOnly.of(true),
        EditorView.editable.of(false),
        EditorView.lineWrapping,
        EditorView.updateListener.of((update) => {
          if (!update.selectionSet || !onSelection) return;
          const range = update.state.selection.main;
          if (range.empty) {
            onSelection(undefined);
            return;
          }
          const start = update.state.doc.lineAt(range.from);
          const end = update.state.doc.lineAt(Math.max(range.from, range.to - 1));
          onSelection({
            path: file.path,
            startLine: start.number,
            endLine: end.number,
            text: update.state.sliceDoc(range.from, range.to),
          });
        }),
      ],
    });
    const view = new EditorView({ state, parent: host.current });
    if (focusLine && focusLine > 0) {
      const line = view.state.doc.line(Math.min(focusLine, view.state.doc.lines));
      view.dispatch({ selection: { anchor: line.from }, effects: EditorView.scrollIntoView(line.from, { y: "center" }) });
    }
    return () => view.destroy();
  }, [file, focusLine, onSelection]);
  return <div className="code-viewer" ref={host} aria-label={`${file.path} 只读预览`} />;
}
