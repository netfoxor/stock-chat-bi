import { create } from "zustand";
import { api } from "../api/client";

export type WidgetType = "chart" | "table";

export type Widget = {
  id: number;
  user_id: number;
  title: string;
  type: WidgetType;
  data: any;
  layout: any;
};

type DashboardState = {
  widgets: Widget[];
  setWidgets: (w: Widget[]) => void;
  fetchWidgets: () => Promise<void>;
  addWidget: (w: { title?: string; type: WidgetType; data: any; layout: any }) => Promise<void>;
  updateWidget: (id: number, patch: { title?: string; layout?: any }) => Promise<void>;
  deleteWidget: (id: number) => Promise<void>;
  updateLayoutBatch: (layout: any[]) => Promise<void>;
};

export const useDashboardStore = create<DashboardState>((set, get) => ({
  widgets: [],
  setWidgets: (w) => set({ widgets: w }),

  fetchWidgets: async () => {
    const res = await api.get<Widget[]>("/dashboard/widgets");
    set({ widgets: res.data });
  },

  addWidget: async (w) => {
    const res = await api.post<Widget>("/dashboard/widgets", w);
    set({ widgets: [...get().widgets, res.data] });
  },

  updateWidget: async (id, patch) => {
    const res = await api.put<Widget>(`/dashboard/widgets/${id}`, patch);
    set({ widgets: get().widgets.map((x) => (x.id === id ? res.data : x)) });
  },

  deleteWidget: async (id) => {
    await api.delete(`/dashboard/widgets/${id}`);
    set({ widgets: get().widgets.filter((x) => x.id !== id) });
  },

  updateLayoutBatch: async (layout) => {
    await api.put("/dashboard/layout", { layout });
  },
}));

