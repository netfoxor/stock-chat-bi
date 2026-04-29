import { Button, Divider, Layout, List, Modal, Space, Typography, Checkbox, message } from "antd";
import { MinusOutlined, RobotOutlined } from "@ant-design/icons";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api/client";
import { parseApiErrorMessage } from "../../api/error";
import { domRectToSpot, OnboardingSpotlight, type SpotRect } from "../../components/OnboardingSpotlight";
import { useSSE } from "../../hooks/useSSE";
import { useChatStore } from "../../store/chatStore";
import { ONBOARDING_PRESET_MESSAGE, useOnboardingStore } from "../../store/onboardingStore";
import { ChatInput } from "./ChatInput";
import type { MessageOnboardingHighlight } from "./MessageItem";
import { MessageItem } from "./MessageItem";
import { extractSpecialBlocks } from "./renderers";

function findFirstAddableMessage(messages: any[]): { id: number; kind: "chart" | "table" } | null {
  for (const m of messages) {
    const { blocks } = extractSpecialBlocks(m.content ?? "");
    const hasE = blocks.some((b) => b.kind === "echarts");
    const hasT = blocks.some((b) => b.kind === "datatable");
    if (hasE) return { id: m.id, kind: "chart" };
    if (hasT) return { id: m.id, kind: "table" };
  }
  return null;
}

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
  const upsertStreamingTrace = useChatStore((s) => s.upsertStreamingTrace);
  const applyFinalTrace = useChatStore((s) => s.applyFinalTrace);
  const clearStreaming = useChatStore((s) => s.clearStreaming);

  const phase = useOnboardingStore((s) => s.phase);
  const setOnboardingPhase = useOnboardingStore((s) => s.setPhase);
  const skipOnboarding = useOnboardingStore((s) => s.skipOnboarding);

  const [loadingConvs, setLoadingConvs] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const [editingConvId, setEditingConvId] = useState<number | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{ startX: number; startY: number; baseLeft: number; baseTop: number; dragging: boolean } | null>(
    null,
  );
  const [pos, setPos] = useState<{ left: number; top: number }>({ left: 0, top: 0 });
  const [open, setOpen] = useState(false);
  const [size, setSize] = useState<{ width: number; height: number }>({ width: 520, height: 640 });

  const sendBtnRef = useRef<HTMLAnchorElement | HTMLButtonElement | null>(null);
  const panelFreezeRef = useRef(false);
  const [sendHole, setSendHole] = useState<SpotRect | null>(null);
  const [addHole, setAddHole] = useState<SpotRect | null>(null);

  const reportAddHole = useCallback((r: SpotRect | null) => {
    setAddHole(r);
  }, []);

  const onboardingTarget = useMemo(() => findFirstAddableMessage(messages), [messages]);

  const addHighlight = useMemo<MessageOnboardingHighlight | null>(() => {
    if (phase !== "add_widget" || !onboardingTarget) return null;
    return {
      kind: onboardingTarget.kind,
      onHoleRect: reportAddHole,
    };
  }, [phase, onboardingTarget, reportAddHole]);

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
    let cid = activeConversationId;
    if (!cid) {
      const res = await api.post("/conversations", {});
      await loadConversations();
      cid = res.data.id;
      setActiveConversationId(cid);
    }

    const guideSend = useOnboardingStore.getState().phase === "send";
    if (guideSend) {
      useOnboardingStore.getState().setPhase("add_widget");
    }

    appendMessage({
      id: Date.now(),
      role: "user",
      content: text,
      content_type: "text",
      created_at: new Date().toISOString(),
    } as any);

    clearStreaming();

    try {
      await start(
        "/chat/stream",
        { conversation_id: cid, message: text },
        (evt) => {
          if (evt.type === "trace") {
            upsertStreamingTrace(evt.event);
          }
          if (evt.type === "delta") {
            upsertStreamingAssistant(evt.content);
          }
          if (evt.type === "done") {
            const d = evt as { trace?: unknown };
            if (Array.isArray(d.trace)) applyFinalTrace(d.trace);
            loadMessages(cid!).catch(() => void 0);
          }
        },
      );
    } catch (err) {
      message.error(parseApiErrorMessage(err, "对话生成失败"));
      clearStreaming();
    }
  };

  const remeasureSendHole = useCallback(() => {
    if (phase !== "send" || !open) {
      setSendHole(null);
      return;
    }
    const el = sendBtnRef.current;
    if (!el) {
      setSendHole(null);
      return;
    }
    setSendHole(domRectToSpot(el.getBoundingClientRect()));
  }, [phase, open]);

  const prevChatOpenRef = useRef(open);
  useLayoutEffect(() => {
    const wasOpen = prevChatOpenRef.current;
    prevChatOpenRef.current = open;
    if (phase === "send" && open && !wasOpen) {
      panelFreezeRef.current = true;
    }
    if (!open) {
      panelFreezeRef.current = false;
    }
  }, [phase, open]);

  useEffect(() => {
    if (phase !== "send" || !open) return;
    const tid = window.setTimeout(() => {
      panelFreezeRef.current = false;
      remeasureSendHole();
      window.setTimeout(remeasureSendHole, 50);
      window.setTimeout(remeasureSendHole, 140);
    }, 290);
    return () => window.clearTimeout(tid);
  }, [phase, open, remeasureSendHole]);

  useLayoutEffect(() => {
    if (phase !== "send" || !open) return;
    if (panelFreezeRef.current) return;
    remeasureSendHole();
  }, [pos, size, messages, running, phase, open, remeasureSendHole]);

  useEffect(() => {
    if (phase !== "send" || !open) return;
    const sc = scrollRef.current;
    sc?.addEventListener("scroll", remeasureSendHole, { passive: true });
    window.addEventListener("resize", remeasureSendHole);
    return () => {
      sc?.removeEventListener("scroll", remeasureSendHole);
      window.removeEventListener("resize", remeasureSendHole);
    };
  }, [phase, open, remeasureSendHole]);

  /** 打开弹窗后立即 layout 仍会处在 scale/opacity 动画中间，defer 坐标；拖拽与消息变化在非冻结时再量测 */
  const inputDisabled = phase !== "send" && !activeConversationId;
  const presetText = phase === "send" ? ONBOARDING_PRESET_MESSAGE : null;
  const showFabMask = phase === "fab";
  const showSendMask = phase === "send" && open;

  const fabZ = phase === "fab" ? 10060 : 1001;
  const panelZ = 1000;

  return (
    <>
      {showFabMask && (
        <>
          <div
            role="presentation"
            style={{
              position: "fixed",
              inset: 0,
              zIndex: 10058,
              background: "rgba(0, 0, 0, 0.58)",
              pointerEvents: "auto",
            }}
          />
          <div
            style={{
              position: "fixed",
              bottom: "max(100px, 14vh)",
              right: 20,
              zIndex: 10059,
              maxWidth: 300,
              textAlign: "right",
              pointerEvents: "auto",
            }}
          >
            <Typography.Text
              style={{
                color: "#fff",
                fontSize: 15,
                textShadow: "0 1px 3px rgba(0,0,0,0.5)",
                display: "block",
                marginBottom: 4,
              }}
            >
              请点击右下角的「智能助手」打开对话
            </Typography.Text>
            <Button type="link" size="small" onClick={skipOnboarding} style={{ color: "#91d5ff", padding: 0, height: "auto" }}>
              跳过指引
            </Button>
          </div>
        </>
      )}

      <OnboardingSpotlight
        visible={showSendMask}
        hole={sendHole}
        title='已为你填写示例问题，请点击「发送」'
        zBase={10058}
        onSkip={skipOnboarding}
      />
      <OnboardingSpotlight
        visible={phase === "add_widget" && open}
        hole={addHole}
        title="点击这里把此图表/表格添加到大屏"
        zBase={10058}
        fallbackFullWhenHoleMissing={false}
        onSkip={skipOnboarding}
      />

      <div style={{ position: "fixed", right: 16, bottom: 16, zIndex: fabZ }}>
        <Button
          shape="circle"
          icon={<RobotOutlined />}
          disabled={open}
          onClick={() => {
            setOpen(true);
            if (phase === "fab") {
              setOnboardingPhase("send");
            }
          }}
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
        onTransitionEnd={(e) => {
          if (!open || phase !== "send") return;
          if (e.target !== e.currentTarget) return;
          if (e.propertyName !== "opacity" && e.propertyName !== "transform") return;
          requestAnimationFrame(() => requestAnimationFrame(() => remeasureSendHole()));
        }}
        style={{
          position: "fixed",
          left: pos.left,
          top: pos.top,
          width: size.width,
          height: size.height,
          zIndex: panelZ,
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
              <Button size="small" icon={<MinusOutlined />} onClick={() => setOpen(false)} title="最小化" />
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
                      <Space style={{ width: "100%", justifyContent: "space-between" }}>
                        {editingConvId === c.id ? (
                          <input
                            value={editingTitle}
                            onChange={(e) => setEditingTitle(e.target.value)}
                            autoFocus
                            style={{
                              width: 110,
                              fontSize: 12,
                              padding: "2px 6px",
                              border: "1px solid #d9d9d9",
                              borderRadius: 6,
                            }}
                            onBlur={async () => {
                              const next = (editingTitle || "").trim() || c.title;
                              setEditingConvId(null);
                              if (next !== c.title) {
                                await api.put(`/conversations/${c.id}`, { title: next });
                                await loadConversations();
                              }
                            }}
                            onKeyDown={async (e) => {
                              if (e.key === "Enter") {
                                (e.currentTarget as any).blur();
                              }
                              if (e.key === "Escape") {
                                setEditingConvId(null);
                                setEditingTitle("");
                              }
                            }}
                          />
                        ) : (
                          <Typography.Text
                            ellipsis
                            style={{ maxWidth: 110 }}
                            onDoubleClick={(e) => {
                              e.stopPropagation();
                              setEditingConvId(c.id);
                              setEditingTitle(c.title);
                            }}
                          >
                            {c.title}
                          </Typography.Text>
                        )}
                        <Button
                          size="small"
                          danger
                          onClick={(e) => {
                            e.stopPropagation();
                            Modal.confirm({
                              title: "删除会话？",
                              content: "删除后该会话的所有消息将一起删除，且无法恢复。",
                              okText: "删除",
                              okButtonProps: { danger: true },
                              cancelText: "取消",
                              onOk: async () => {
                                await api.delete(`/conversations/${c.id}`);
                                await loadConversations();
                                if (activeConversationId === c.id) {
                                  setActiveConversationId(null);
                                }
                              },
                            });
                          }}
                        >
                          删除
                        </Button>
                      </Space>
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
                  <MessageItem
                    key={`${m.id}-${m.created_at}`}
                    message={m}
                    onboardingHighlight={
                      onboardingTarget && m.id === onboardingTarget.id ? addHighlight : null
                    }
                  />
                ))}
                {running && (
                  <Typography.Text type="secondary" style={{ display: "block", padding: 8 }}>
                    正在生成中…
                  </Typography.Text>
                )}
              </div>

              <div style={{ padding: 10, borderTop: "1px solid #f0f0f0" }}>
                <ChatInput
                  onSend={onSend}
                  disabled={inputDisabled}
                  presetText={presetText}
                  onboardingInputReadOnly={phase === "send"}
                  sendButtonRef={sendBtnRef}
                />
              </div>
            </Layout.Content>
          </Layout>
        </Layout>
      </div>
    </>
  );
}
