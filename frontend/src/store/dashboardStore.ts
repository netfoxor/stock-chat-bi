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
    const res = await api.post<Widget>("/dashboard/widgets", { ...w, dashboard_id: did });
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

