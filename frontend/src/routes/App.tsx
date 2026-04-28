import { ConfigProvider, Layout } from "antd";
import zhCN from "antd/locale/zh_CN";
import { Navigate, Route, Routes } from "react-router-dom";
import { useAuthStore } from "../store/authStore";
import { LoginPage } from "../views/LoginPage";
import { DashboardPage } from "../views/DashboardPage";

export function App() {
  const token = useAuthStore((s) => s.token);

  return (
    <ConfigProvider locale={zhCN}>
      <Layout style={{ minHeight: "100vh" }}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={token ? <DashboardPage /> : <Navigate to="/login" replace />} />
        </Routes>
      </Layout>
    </ConfigProvider>
  );
}

