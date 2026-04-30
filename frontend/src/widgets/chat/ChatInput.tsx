import { Button, Input, Space } from "antd";
import { type LegacyRef, useEffect, useRef, useState } from "react";

export function ChatInput(props: {
  onSend: (text: string) => Promise<void>;
  disabled?: boolean;
  /** 新手引导：预填文案 */
  presetText?: string | null;
  /** 从外部一键填入候选问题（如新会话占位示例）；按 nonce 递增触发，不占位引导 */
  quickFill?: { nonce: number; text: string } | null;
  /** 仅允许点击发送时使用 */
  onboardingInputReadOnly?: boolean;
  sendButtonRef?: LegacyRef<HTMLAnchorElement | HTMLButtonElement>;
}) {
  const [value, setValue] = useState("");
  const [sending, setSending] = useState(false);
  const lastQuickNonceRef = useRef<number | null>(null);

  useEffect(() => {
    if (props.presetText != null && props.presetText !== "") {
      setValue(props.presetText);
    }
  }, [props.presetText]);

  useEffect(() => {
    const q = props.quickFill;
    if (q == null) return;
    if (lastQuickNonceRef.current === q.nonce) return;
    lastQuickNonceRef.current = q.nonce;
    setValue(q.text);
  }, [props.quickFill]);

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

  const inputReadOnly = props.onboardingInputReadOnly ?? false;

  return (
    <Space.Compact style={{ width: "100%" }}>
      <Input
        placeholder="问我：茅台最近一个月的历史行情"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onPressEnter={() => {
          if (!inputReadOnly) void send();
        }}
        readOnly={inputReadOnly}
        disabled={props.disabled}
      />
      <Button ref={props.sendButtonRef} type="primary" onClick={() => void send()} loading={sending} disabled={props.disabled}>
        发送
      </Button>
    </Space.Compact>
  );
}
