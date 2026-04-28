import { Button, Input, Space } from "antd";
import { useState } from "react";

export function ChatInput(props: { onSend: (text: string) => Promise<void>; disabled?: boolean }) {
  const [value, setValue] = useState("");
  const [sending, setSending] = useState(false);

  const send = async () => {
    const text = value.trim();
    if (!text) return;
    setSending(true);
    try {
      setValue("");
      await props.onSend(text);
    } finally {
      setSending(false);
    }
  };

  return (
    <Space.Compact style={{ width: "100%" }}>
      <Input
        placeholder="问我：茅台最近一个月收盘价走势"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onPressEnter={() => void send()}
        disabled={props.disabled}
      />
      <Button type="primary" onClick={() => void send()} loading={sending} disabled={props.disabled}>
        发送
      </Button>
    </Space.Compact>
  );
}

