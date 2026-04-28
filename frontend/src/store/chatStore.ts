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
        created_at: new Date().toISOString(),
      });
    } else {
      last.content = (last.content ?? "") + delta;
    }
    set({ messages: msgs });
  },

  clearStreaming: () => {
    const msgs = get().messages.filter((m) => m.id !== -1);
    set({ messages: msgs });
  },
}));

