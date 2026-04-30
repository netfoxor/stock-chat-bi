import { create } from "zustand";
import { api } from "../api/client";

export type WidgetType = "chart" | "table";

export type Dashboard = {
  id: number;
  user_id: number;
  name: string;
  created_at: string;
  updated_at: string;
};

export type Widget = {
  id: number;
  user_id: number;
  dashboard_id?: number | null;
  title: string;
  type: WidgetType;
  data: any;
  layout: any;
  config?: any;
  /** 后端可选返回，用于强刷子组件 key */
  updated_at?: string | null;
};

type DashboardState = {
  dashboards: Dashboard[];
  activeDashboardId: number | null;
  setActiveDashboardId: (id: number | null) => void;
  fetchDashboards: () => Promise<void>;
  createDashboard: (name?: string) => Promise<Dashboard>;
  renameDashboard: (id: number, name: string) => Promise<void>;
  deleteDashboard: (id: number) => Promise<void>;

  widgets: Widget[];
  setWidgets: (w: Widget[]) => void;
  fetchWidgets: (dashboardId?: number | null) => Promise<void>;
  addWidget: (w: { title?: string; type: WidgetType; data: any; layout: any; config?: any }) => Promise<void>;
  updateWidget: (id: number, patch: { title?: string; layout?: any; data?: any; config?: any }) => Promise<void>;
  deleteWidget: (id: number) => Promise<void>;
  updateLayoutBatch: (layout: any[]) => Promise<void>;
};

export const useDashboardStore = create<DashboardState>((set, get) => ({
  dashboards: [],
  activeDashboardId: null,
  setActiveDashboardId: (id) => set({ activeDashboardId: id, widgets: [] }),

  fetchDashboards: async () => {
    const res = await api.get<Dashboard[]>("/dashboard/dashboards");
    set({ dashboards: res.data });
    if (!get().activeDashboardId && res.data.length > 0) {
      set({ activeDashboardId: res.data[0].id });
    }
  },

  createDashboard: async (name) => {
    const res = await api.post<Dashboard>("/dashboard/dashboards", { name });
    set({ dashboards: [res.data, ...get().dashboards], activeDashboardId: res.data.id });
    return res.data;
  },

  renameDashboard: async (id, name) => {
    const res = await api.put<Dashboard>(`/dashboard/dashboards/${id}`, { name });
    set({ dashboards: get().dashboards.map((d) => (d.id === id ? res.data : d)) });
  },

  deleteDashboard: async (id) => {
    await api.delete(`/dashboard/dashboards/${id}`);
    const remaining = get().dashboards.filter((d) => d.id !== id);
    set({ dashboards: remaining, activeDashboardId: remaining[0]?.id ?? null, widgets: [] });
  },

  widgets: [],
  setWidgets: (w) => set({ widgets: w }),

  fetchWidgets: async (dashboardId) => {
    const did = dashboardId ?? get().activeDashboardId;
    const res = await api.get<Widget[]>("/dashboard/widgets", { params: did ? { dashboard_id: did } : {} });
    set({ widgets: res.data });
  },

  addWidget: async (w) => {
    const did = get().activeDashboardId;
    const existing = get().widgets;

    const raw = (w.layout && typeof w.layout === "object" ? w.layout : {}) as Record<string, unknown>;
    const lw = typeof raw.w === "number" && Number.isFinite(raw.w) ? raw.w : 6;
    const lh = typeof raw.h === "number" && Number.isFinite(raw.h) ? raw.h : 8;

    let maxBottom = 0;
    for (const ww of existing) {
      const lay = ww.layout as Record<string, unknown> | null | undefined;
      if (!lay || typeof lay !== "object") continue;
      const y = typeof lay.y === "number" && Number.isFinite(lay.y) ? lay.y : 0;
      const hh = typeof lay.h === "number" && Number.isFinite(lay.h) ? lay.h : 8;
      maxBottom = Math.max(maxBottom, y + hh);
    }

    const layout: Record<string, unknown> = { ...raw, x: 0, y: maxBottom, w: lw, h: lh };
    delete layout.i;

    const res = await api.post<Widget>("/dashboard/widgets", { ...w, dashboard_id: did, layout });
    set({ widgets: [...get().widgets, res.data] });
  },

  updateWidget: async (id, patch) => {
    const prev = get().widgets.find((x) => x.id === id);
    const res = await api.put<Widget>(`/dashboard/widgets/${id}`, patch);
    const raw = res.data.layout as Record<string, unknown> | undefined;
    const layOk =
      raw &&
      typeof raw === "object" &&
      ["x", "y", "w", "h"].every((k) => typeof raw[k] === "number" && Number.isFinite(raw[k] as number));
    const merged: Widget =
      layOk || !prev
        ? res.data
        : { ...res.data, layout: prev.layout ?? res.data.layout };
    set({ widgets: get().widgets.map((x) => (x.id === id ? merged : x)) });
  },

  deleteWidget: async (id) => {
    await api.delete(`/dashboard/widgets/${id}`);
    set({ widgets: get().widgets.filter((x) => x.id !== id) });
  },

  updateLayoutBatch: async (layout) => {
    await api.put("/dashboard/layout", { layout });
    set((state) => ({
      widgets: state.widgets.map((w) => {
        const item = layout.find((li: { i?: string }) => String(li?.i ?? "") === String(w.id));
        if (!item) return w;
        return { ...w, layout: { ...(w.layout && typeof w.layout === "object" ? w.layout : {}), ...item } };
      }),
    }));
  },
}));

