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
  /** 流结束时用服务端完整 trace 覆盖（修正漏合并的「调用中」等） */
  applyFinalTrace: (trace: any[]) => void;
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
      const rowKeyFor = (row: any): string | null => {
        const tk = row?.meta?.trace_key;
        if (typeof tk === "string" && tk.length > 0) return tk;
        const sid = row?.meta?.span_id;
        if (sid != null && String(sid).length > 0) return `__span:${String(sid)}`;
        return null;
      };
      const evKey = rowKeyFor(ev);
      const evSpan = ev?.meta?.span_id != null ? String(ev.meta.span_id) : null;
      if (evKey || evSpan) {
        const idx = trace.findIndex((x: any) => {
          const xk = rowKeyFor(x);
          if (evKey && xk && evKey === xk) return true;
          if (evSpan != null && x?.meta?.span_id != null && String(x.meta.span_id) === evSpan) return true;
          return false;
        });
        if (idx >= 0) {
          const prev = trace[idx];
          const mergedMeta = { ...(prev?.meta ?? {}), ...(ev?.meta ?? {}) };
          const endAt = ev.ended_at ?? prev?.ended_at;
          if (endAt != null && String(endAt).length > 0) mergedMeta.phase = "end";

          trace[idx] = {
            ...prev,
            ...ev,
            started_at: ev.started_at ?? prev.started_at,
            ended_at: ev.ended_at ?? prev.ended_at,
            input: ev.input !== undefined && ev.input !== null ? ev.input : prev.input,
            output: ev.output !== undefined && ev.output !== null ? ev.output : prev.output,
            meta: mergedMeta,
          };
        } else {
          trace.push(ev);
        }
      } else {
        trace.push(ev);
      }
    }
    set({ messages: msgs });
  },

  applyFinalTrace: (incoming) => {
    if (!Array.isArray(incoming)) return;
    const msgs = [...get().messages];
    const last = msgs[msgs.length - 1];
    if (!last || last.role !== "assistant" || last.id !== -1) return;
    last.extra = { ...(last.extra ?? {}), trace: [...incoming] };
    set({ messages: msgs });
  },

  clearStreaming: () => {
    const msgs = get().messages.filter((m) => m.id !== -1);
    set({ messages: msgs });
  },
}));

