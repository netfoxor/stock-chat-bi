import CodeMirror from "@uiw/react-codemirror";
import { json } from "@codemirror/lang-json";

type Props = {
  value: string;
  onChange: (v: string) => void;
  height?: number;
};

export function JsonConfigEditor(props: Props) {
  const h = props.height ?? 380;

  return (
    <CodeMirror
      value={props.value}
      height={`${h}px`}
      theme="light"
      extensions={[json()]}
      basicSetup={{ tabSize: 2, lineNumbers: true, foldGutter: true }}
      onChange={(v) => props.onChange(v)}
    />
  );
}
