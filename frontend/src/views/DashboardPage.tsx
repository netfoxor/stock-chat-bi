import { Button, ConfigProvider, Input, Layout, Modal, Select, Space, Tooltip, Typography, message } from "antd";
import { useEffect, useMemo, useState } from "react";
import { APP_DISPLAY_NAME } from "../constants/branding";
import { useAuthStore } from "../store/authStore";
import { ChatWindow } from "../widgets/chat/ChatWindow";
import { DashboardGrid } from "../widgets/dashboard/DashboardGrid";
import { useDashboardStore } from "../store/dashboardStore";

export function DashboardPage() {
  const logout = useAuthStore((s) => s.logout);
  const dashboards = useDashboardStore((s) => s.dashboards);
  const activeDashboardId = useDashboardStore((s) => s.activeDashboardId);
  const setActiveDashboardId = useDashboardStore((s) => s.setActiveDashboardId);
  const fetchDashboards = useDashboardStore((s) => s.fetchDashboards);
  const createDashboard = useDashboardStore((s) => s.createDashboard);
  const renameDashboard = useDashboardStore((s) => s.renameDashboard);
  const deleteDashboard = useDashboardStore((s) => s.deleteDashboard);
  const fetchWidgets = useDashboardStore((s) => s.fetchWidgets);

  const [renameOpen, setRenameOpen] = useState(false);
  const [renameVal, setRenameVal] = useState("");

  const activeName = useMemo(() => dashboards.find((d) => d.id === activeDashboardId)?.name ?? "", [dashboards, activeDashboardId]);

  const onlyOneDashboard = dashboards.length <= 1;
  const deleteDisabled = !activeDashboardId || onlyOneDashboard;

  const headerButtonDisabledTheme = {
    components: {
      Button: {
        colorTextDisabled: "rgba(255, 255, 255, 0.72)",
        colorBgContainerDisabled: "rgba(255, 255, 255, 0.12)",
        borderColorDisabled: "rgba(255, 255, 255, 0.38)",
        opacityDisabled: 1,
      },
    },
  } as const;

  useEffect(() => {
    fetchDashboards()
      .then(() => fetchWidgets())
      .catch((e) => message.error(String(e)));
  }, [fetchDashboards, fetchWidgets]);

  useEffect(() => {
    if (activeDashboardId) fetchWidgets(activeDashboardId).catch(() => void 0);
  }, [activeDashboardId, fetchWidgets]);

  return (
    <Layout style={{ height: "100vh" }}>
      <Layout.Header style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <ConfigProvider theme={headerButtonDisabledTheme}>
          <Space align="center">
            <Typography.Title
              level={4}
              style={{ color: "#fff", margin: 0, fontWeight: 700, letterSpacing: 0.5 }}
            >
              {APP_DISPLAY_NAME}
            </Typography.Title>
            <Select
              value={activeDashboardId ?? undefined}
              style={{ width: 220 }}
              placeholder="选择大屏"
              options={dashboards.map((d) => ({ value: d.id, label: d.name }))}
              onChange={(v) => setActiveDashboardId(v)}
            />
            <Button
              size="small"
              onClick={() => {
                createDashboard("新大屏").catch((e) => message.error(String(e)));
              }}
            >
              新建大屏
            </Button>
            <Button
              size="small"
              disabled={!activeDashboardId}
              onClick={() => {
                setRenameVal(activeName);
                setRenameOpen(true);
              }}
            >
              重命名
            </Button>
            <Tooltip title={onlyOneDashboard ? "至少需要保留一个大屏" : undefined}>
              <span style={{ display: "inline-block" }}>
                <Button
                  size="small"
                  danger={!deleteDisabled}
                  disabled={deleteDisabled}
                  onClick={() => {
                    Modal.confirm({
                      title: "删除大屏？",
                      content: "删除后该大屏下的组件也会一起删除，且无法恢复。",
                      okText: "删除",
                      okButtonProps: { danger: true },
                      cancelText: "取消",
                      onOk: async () => {
                        if (!activeDashboardId) return;
                        try {
                          await deleteDashboard(activeDashboardId);
                        } catch (e: unknown) {
                          const ax = e as { response?: { data?: { detail?: unknown } } };
                          const d = ax.response?.data?.detail;
                          const txt =
                            typeof d === "string"
                              ? d
                              : Array.isArray(d)
                                ? String(d.map((x) => (typeof x === "object" ? JSON.stringify(x) : x)).join("; "))
                                : undefined;
                          message.error(txt ?? String(e));
                          throw e;
                        }
                      },
                    });
                  }}
                >
                  删除大屏
                </Button>
              </span>
            </Tooltip>
          </Space>
        </ConfigProvider>
        <Button
          onClick={() => {
            logout();
            window.location.href = "/login";
          }}
        >
          退出
        </Button>
      </Layout.Header>

      <Layout>
        <Layout.Content style={{ padding: 12 }}>
          <DashboardGrid />
        </Layout.Content>
      </Layout>
      <ChatWindow />

      <Modal
        open={renameOpen}
        title="重命名大屏"
        okText="保存"
        cancelText="取消"
        onCancel={() => setRenameOpen(false)}
        onOk={async () => {
          if (!activeDashboardId) return;
          await renameDashboard(activeDashboardId, renameVal.trim() || "未命名大屏");
          setRenameOpen(false);
        }}
      >
        <Input value={renameVal} onChange={(e) => setRenameVal(e.target.value)} placeholder="输入大屏名称" />
      </Modal>
    </Layout>
  );
}

