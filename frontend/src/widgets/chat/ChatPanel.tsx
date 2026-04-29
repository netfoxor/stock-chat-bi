import { Button, Divider, List, Space, Typography, message } from "antd";
import { useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import { useSSE } from "../../hooks/useSSE";
import { useChatStore } from "../../store/chatStore";
import { ChatInput } from "./ChatInput";
import { MessageItem } from "./MessageItem";

export function ChatPanel() {
  const { start, running } = useSSE();
  const conversations = useChatStore((s) => s.conversations);
  const setConversations = useChatStore((s) => s.setConversations);
  const activeConversationId = useChatStore((s) => s.activeConversationId);
  const setActiveConversationId = useChatStore((s) => s.setActiveConversationId);
  const messages = useChatStore((s) => s.messages);
  const setMessages = useChatStore((s) => s.setMessages);
  const appendMessage = useChatStore((s) => s.appendMessage);
  const upsertStreamingAssistant = useChatStore((s) => s.upsertStreamingAssistant);
  const upsertStreamingTrace = useChatStore((s) => s.upsertStreamingTrace);
  const applyFinalTrace = useChatStore((s) => s.applyFinalTrace);
  const clearStreaming = useChatStore((s) => s.clearStreaming);

  const [loadingConvs, setLoadingConvs] = useState(false);

  const activeTitle = useMemo(() => conversations.find((c) => c.id === activeConversationId)?.title, [conversations, activeConversationId]);

  const loadConversations = async () => {
    setLoadingConvs(true);
    try {
      const res = await api.get("/conversations");
      setConversations(res.data);
      if (!activeConversationId && res.data.length > 0) {
        setActiveConversationId(res.data[0].id);
      }
    } finally {
      setLoadingConvs(false);
    }
  };

  const loadMessages = async (conversationId: number) => {
    const res = await api.get(`/conversations/${conversationId}/messages`);
    setMessages(res.data);
  };

  useEffect(() => {
    loadConversations().catch((e) => message.error(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (activeConversationId) loadMessages(activeConversationId).catch(() => void 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeConversationId]);

  const onNewConversation = async () => {
    const res = await api.post("/conversations", {});
    await loadConversations();
    setActiveConversationId(res.data.id);
  };

  const onSend = async (text: string) => {
    if (!activeConversationId) {
      await onNewConversation();
      return;
    }
    appendMessage({
      id: Date.now(),
      role: "user",
      content: text,
      content_type: "text",
      created_at: new Date().toISOString(),
    } as any);

    clearStreaming();

    await start(
      "/chat/stream",
      { conversation_id: activeConversationId, message: text },
      (evt) => {
        if (evt.type === "trace") {
          upsertStreamingTrace(evt.event);
        }
        if (evt.type === "delta") {
          upsertStreamingAssistant(evt.content);
        }
        if (evt.type === "done") {
          if (Array.isArray(evt.trace)) {
            applyFinalTrace(evt.trace);
          }
          loadMessages(activeConversationId).catch(() => void 0);
        }
      },
    );
  };

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div style={{ padding: 12 }}>
        <Space style={{ width: "100%", justifyContent: "space-between" }}>
          <Typography.Text strong>{activeTitle ?? "会话"}</Typography.Text>
          <Button size="small" onClick={onNewConversation} loading={loadingConvs}>
            新建
          </Button>
        </Space>
        <Divider style={{ margin: "12px 0" }} />

        <List
          size="small"
          bordered
          dataSource={conversations}
          style={{ maxHeight: 140, overflow: "auto" }}
          renderItem={(c: any) => (
            <List.Item
              onClick={() => setActiveConversationId(c.id)}
              style={{
                cursor: "pointer",
                background: c.id === activeConversationId ? "#f5f5f5" : "transparent",
              }}
            >
              <Typography.Text ellipsis style={{ maxWidth: 320 }}>
                {c.title}
              </Typography.Text>
            </List.Item>
          )}
        />
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: "0 12px 12px" }}>
        {messages.map((m: any) => (
          <MessageItem key={`${m.id}-${m.created_at}`} message={m} />
        ))}
        {running && (
          <Typography.Text type="secondary" style={{ display: "block", padding: 8 }}>
            正在生成中…
          </Typography.Text>
        )}
      </div>

      <div style={{ padding: 12, borderTop: "1px solid #f0f0f0" }}>
        <ChatInput onSend={onSend} disabled={!activeConversationId} />
      </div>
    </div>
  );
}

