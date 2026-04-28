import { create } from "zustand";

export type ChatMode = "sidebar" | "float";

export type Conversation = {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
};

export type Message = {
  id: number;
  role: "user" | "assistant";
  content: string;
  content_type: "text" | "chart" | "table";
  extra?: any;
  created_at: string;
};

type ChatState = {
  mode: ChatMode;
  setMode: (m: ChatMode) => void;

  conversations: Conversation[];
  setConversations: (c: Conversation[]) => void;

  activeConversationId: number | null;
  setActiveConversationId: (id: number | null) => void;

  messages: Message[];
  setMessages: (m: Message[]) => void;
  appendMessage: (m: Message) => void;
  upsertStreamingAssistant: (delta: string) => void;
  upsertStreamingTrace: (ev: any) => void;
  clearStreaming: () => void;
};

export const useChatStore = create<ChatState>((set, get) => ({
  mode: "sidebar",
  setMode: (m) => set({ mode: m }),

  conversations: [],
  setConversations: (c) => set({ conversations: c }),

  activeConversationId: null,
  setActiveConversationId: (id) => set({ activeConversationId: id, messages: [] }),

  messages: [],
  setMessages: (m) => set({ messages: m }),
  appendMessage: (m) => set({ messages: [...get().messages, m] }),

  upsertStreamingAssistant: (delta) => {
    const msgs = [...get().messages];
    const last = msgs[msgs.length - 1];
    if (!last || last.role !== "assistant" || last.id !== -1) {
      msgs.push({
        id: -1,
        role: "assistant",
        content: delta,
        content_type: "text",
        extra: { trace: [] as any[] },
        created_at: new Date().toISOString(),
      });
    } else {
      last.content = (last.content ?? "") + delta;
    }
    set({ messages: msgs });
  },

  upsertStreamingTrace: (ev) => {
    const msgs = [...get().messages];
    const last = msgs[msgs.length - 1];
    if (!last || last.role !== "assistant" || last.id !== -1) {
      msgs.push({
        id: -1,
        role: "assistant",
        content: "",
        content_type: "text",
        extra: { trace: [ev] },
        created_at: new Date().toISOString(),
      });
    } else {
      const extra = (last.extra ??= {});
      const trace = (extra.trace ??= []);
      const spanId = ev?.meta?.span_id;
      if (spanId) {
        const idx = trace.findIndex((x: any) => x?.meta?.span_id === spanId);
        if (idx >= 0) {
          trace[idx] = { ...trace[idx], ...ev, meta: { ...(trace[idx]?.meta ?? {}), ...(ev?.meta ?? {}) } };
        } else {
          trace.push(ev);
        }
      } else {
        trace.push(ev);
      }
    }
    set({ messages: msgs });
  },

  clearStreaming: () => {
    const msgs = get().messages.filter((m) => m.id !== -1);
    set({ messages: msgs });
  },
}));

