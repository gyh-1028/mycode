import { basicSetup } from "codemirror";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { python } from "@codemirror/lang-python";
import { EditorState } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { useEffect, useRef } from "react";
import { OpenFile } from "./protocol";

function languageExtension(language: string) {
  if (language === "python") return python();
  if (language === "javascript") return javascript({ jsx: true });
  if (language === "typescript") return javascript({ jsx: true, typescript: true });
  if (language === "json") return json();
  if (language === "markdown") return markdown();
  return [];
}

export function CodeViewer({ file }: { file: OpenFile }): React.JSX.Element {
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
      ],
    });
    const view = new EditorView({ state, parent: host.current });
    return () => view.destroy();
  }, [file]);
  return <div className="code-viewer" ref={host} aria-label={`${file.path} 只读预览`} />;
}
