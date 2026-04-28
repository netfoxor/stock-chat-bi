import { Button, Layout, Space, Typography } from "antd";
import { useEffect } from "react";
import { useAuthStore } from "../store/authStore";
import { ChatPanel } from "../widgets/chat/ChatPanel";
import { DashboardGrid } from "../widgets/dashboard/DashboardGrid";
import { useDashboardStore } from "../store/dashboardStore";

export function DashboardPage() {
  const logout = useAuthStore((s) => s.logout);
  const fetchWidgets = useDashboardStore((s) => s.fetchWidgets);

  useEffect(() => {
    fetchWidgets().catch(() => void 0);
  }, [fetchWidgets]);

  return (
    <Layout style={{ height: "100vh" }}>
      <Layout.Header style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <Space>
          <Typography.Text style={{ color: "white" }} strong>
            Stock Chat BI
          </Typography.Text>
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
        <Layout.Sider width={420} theme="light" style={{ borderLeft: "1px solid #f0f0f0" }}>
          <ChatPanel />
        </Layout.Sider>
      </Layout>
    </Layout>
  );
}

