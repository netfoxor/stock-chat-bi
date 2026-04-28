import { Button, Input, Layout, Modal, Select, Space, Typography, message } from "antd";
import { useEffect, useMemo, useState } from "react";
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
        <Space>
          <Typography.Text style={{ color: "white" }} strong>
            Stock Chat BI
          </Typography.Text>
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
          <Button
            size="small"
            danger
            disabled={!activeDashboardId}
            onClick={() => {
              Modal.confirm({
                title: "删除大屏？",
                content: "删除后该大屏下的组件也会一起删除，且无法恢复。",
                okText: "删除",
                okButtonProps: { danger: true },
                cancelText: "取消",
                onOk: async () => {
                  if (!activeDashboardId) return;
                  await deleteDashboard(activeDashboardId);
                },
              });
            }}
          >
            删除大屏
          </Button>
        </Space>
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

