import { Button, Divider, Layout, List, Space, Typography, Checkbox, message } from "antd";
import { MinusOutlined, RobotOutlined } from "@ant-design/icons";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api/client";
import { useSSE } from "../../hooks/useSSE";
import { useChatStore } from "../../store/chatStore";
import { ChatInput } from "./ChatInput";
import { MessageItem } from "./MessageItem";

export function ChatWindow() {
  const { start, running } = useSSE();
  const conversations = useChatStore((s) => s.conversations);
  const setConversations = useChatStore((s) => s.setConversations);
  const activeConversationId = useChatStore((s) => s.activeConversationId);
  const setActiveConversationId = useChatStore((s) => s.setActiveConversationId);
  const messages = useChatStore((s) => s.messages);
  const setMessages = useChatStore((s) => s.setMessages);
  const appendMessage = useChatStore((s) => s.appendMessage);
  const upsertStreamingAssistant = useChatStore((s) => s.upsertStreamingAssistant);
  const clearStreaming = useChatStore((s) => s.clearStreaming);

  const [loadingConvs, setLoadingConvs] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{ startX: number; startY: number; baseLeft: number; baseTop: number; dragging: boolean } | null>(
    null,
  );
  const [pos, setPos] = useState<{ left: number; top: number }>({ left: 0, top: 0 });
  const [open, setOpen] = useState(false); // 默认最小化
  const [size, setSize] = useState<{ width: number; height: number }>({ width: 520, height: 640 });

  const calcCenteredPos = () => ({
    left: Math.max(16, Math.round(window.innerWidth * 0.2)),
    top: Math.max(16, Math.round(window.innerHeight * 0.2)),
  });

  const calcCenteredSize = () => ({
    width: Math.max(420, Math.round(window.innerWidth * 0.6)),
    height: Math.max(420, Math.round(window.innerHeight * 0.6)),
  });

  const activeTitle = useMemo(
    () => conversations.find((c) => c.id === activeConversationId)?.title,
    [conversations, activeConversationId],
  );

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
    // 初始位置：居中（即使默认最小化，也把“展开位置”设好）
    setPos(calcCenteredPos());
    setSize(calcCenteredSize());
  }, []);

  useEffect(() => {
    if (open) {
      setPos(calcCenteredPos());
      setSize(calcCenteredSize());
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (activeConversationId) loadMessages(activeConversationId).catch(() => void 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeConversationId]);

  useEffect(() => {
    if (!autoScroll) return;
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages, autoScroll, running]);

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
        if (evt.type === "delta") {
          upsertStreamingAssistant(evt.content);
        }
        if (evt.type === "done") {
          loadMessages(activeConversationId).catch(() => void 0);
        }
      },
    );
  };

  return (
    <>
      {/* 右下角 AI 助理浮标：打开时置灰不可点 */}
      <div style={{ position: "fixed", right: 16, bottom: 16, zIndex: 1001 }}>
        <Button
          shape="circle"
          icon={<RobotOutlined />}
          disabled={open}
          onClick={() => setOpen(true)}
          style={{
            width: 44,
            height: 44,
            boxShadow: "0 8px 20px rgba(0,0,0,0.12)",
            transition: "transform 200ms ease, opacity 200ms ease",
            opacity: open ? 0.35 : 1,
          }}
        />
      </div>

      <div
        style={{
          position: "fixed",
          left: pos.left,
          top: pos.top,
          width: size.width,
          height: size.height,
          zIndex: 1000,
          background: "white",
          border: "1px solid #e6e6e6",
          borderRadius: 12,
          boxShadow: "0 10px 30px rgba(0,0,0,0.12)",
          overflow: "hidden",
          resize: open ? "both" : "none",
          transformOrigin: "bottom right",
          transition: "transform 220ms ease, opacity 220ms ease",
          opacity: open ? 1 : 0,
          transform: open ? "scale(1)" : "scale(0.2)",
          pointerEvents: open ? "auto" : "none",
        }}
      >
      <Layout style={{ height: "100%" }}>
        <Layout.Header
          style={{
            height: 44,
            padding: "0 12px",
            display: "flex",
            alignItems: "center",
            cursor: "move",
            userSelect: "none",
          }}
          onMouseDown={(e) => {
            // 开始拖动
            dragRef.current = {
              startX: e.clientX,
              startY: e.clientY,
              baseLeft: pos.left,
              baseTop: pos.top,
              dragging: true,
            };
            const onMove = (ev: MouseEvent) => {
              const st = dragRef.current;
              if (!st?.dragging) return;
              const dx = ev.clientX - st.startX;
              const dy = ev.clientY - st.startY;
              const nextLeft = Math.min(Math.max(0, st.baseLeft + dx), window.innerWidth - 240);
              const nextTop = Math.min(Math.max(0, st.baseTop + dy), window.innerHeight - 120);
              setPos({ left: nextLeft, top: nextTop });
            };
            const onUp = () => {
              if (dragRef.current) dragRef.current.dragging = false;
              window.removeEventListener("mousemove", onMove);
              window.removeEventListener("mouseup", onUp);
            };
            window.addEventListener("mousemove", onMove);
            window.addEventListener("mouseup", onUp);
          }}
        >
          <Space style={{ width: "100%", justifyContent: "space-between" }}>
            <Typography.Text style={{ color: "white" }} strong>
              AI 助理
            </Typography.Text>
            <Button
              size="small"
              icon={<MinusOutlined />}
              onClick={() => setOpen(false)}
              title="最小化"
            />
          </Space>
        </Layout.Header>

        <Layout>
          <Layout.Sider width={180} theme="light" style={{ borderRight: "1px solid #f0f0f0" }}>
            <div style={{ padding: 10 }}>
              <Space style={{ width: "100%", justifyContent: "space-between" }}>
                <Typography.Text type="secondary">会话列表</Typography.Text>
                <Button size="small" onClick={onNewConversation} loading={loadingConvs}>
                  新建
                </Button>
              </Space>
              <Divider style={{ margin: "10px 0" }} />
              <List
                size="small"
                bordered
                dataSource={conversations}
                style={{ height: "calc(100% - 44px)", overflow: "auto" }}
                renderItem={(c: any) => (
                  <List.Item
                    onClick={() => setActiveConversationId(c.id)}
                    style={{
                      cursor: "pointer",
                      background: c.id === activeConversationId ? "#f5f5f5" : "transparent",
                    }}
                  >
                    <Typography.Text ellipsis style={{ maxWidth: 140 }}>
                      {c.title}
                    </Typography.Text>
                  </List.Item>
                )}
              />
            </div>
          </Layout.Sider>

          <Layout.Content style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
            <div style={{ padding: 10, borderBottom: "1px solid #f0f0f0" }}>
              <Space style={{ width: "100%", justifyContent: "space-between" }}>
                <Typography.Text strong ellipsis style={{ maxWidth: 220 }}>
                  {activeTitle ?? "未选择会话"}
                </Typography.Text>
                <Checkbox checked={autoScroll} onChange={(e) => setAutoScroll(e.target.checked)}>
                  自动滚屏
                </Checkbox>
              </Space>
            </div>

            <div ref={scrollRef} style={{ flex: 1, overflow: "auto", padding: "0 10px 10px", minHeight: 0 }}>
              {messages.map((m: any) => (
                <MessageItem key={`${m.id}-${m.created_at}`} message={m} />
              ))}
              {running && (
                <Typography.Text type="secondary" style={{ display: "block", padding: 8 }}>
                  正在生成中…
                </Typography.Text>
              )}
            </div>

            <div style={{ padding: 10, borderTop: "1px solid #f0f0f0" }}>
              <ChatInput onSend={onSend} disabled={!activeConversationId} />
            </div>
          </Layout.Content>
        </Layout>
      </Layout>
      </div>
    </>
  );
}

