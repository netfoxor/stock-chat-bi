import { Button, Card, Form, Input, Layout, Space, Typography, message } from "antd";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { parseApiErrorMessage } from "../api/error";
import { APP_DISPLAY_NAME } from "../constants/branding";
import { useAuthStore } from "../store/authStore";

type AuthResp = { access_token: string; token_type: string };

export function LoginPage() {
  const token = useAuthStore((s) => s.token);
  const setToken = useAuthStore((s) => s.setToken);
  const navigate = useNavigate();

  if (token) {
    return <Navigate to="/" replace />;
  }

  const onLogin = async (values: { username: string; password: string }) => {
    try {
      const res = await api.post<AuthResp>("/auth/login", values);
      setToken(res.data.access_token);
      message.success("登录成功");
      navigate("/", { replace: true });
    } catch (err) {
      message.error(parseApiErrorMessage(err, "登录失败"));
    }
  };

  return (
    <Layout.Content
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        minHeight: "100vh",
        padding: 24,
        boxSizing: "border-box",
      }}
    >
      <Typography.Title level={2} style={{ margin: "0 0 20px", fontWeight: 700, textAlign: "center" }}>
        {APP_DISPLAY_NAME}
      </Typography.Title>
      <Card style={{ width: "min(520px, calc(100vw - 48px))" }} variant="outlined">
        <Space direction="vertical" style={{ width: "100%" }} size="large">
          <div style={{ textAlign: "center" }}>
            <Typography.Title level={4} type="secondary" style={{ margin: 0, fontWeight: 500 }}>
              登录
            </Typography.Title>
            <Typography.Text type="secondary" style={{ display: "block", marginTop: 8 }}>
              登录后进入数据大屏与智能分析对话
            </Typography.Text>
          </div>

          <Form layout="vertical" onFinish={onLogin}>
            <Form.Item name="username" label="用户名" rules={[{ required: true }]}>
              <Input autoComplete="username" />
            </Form.Item>
            <Form.Item name="password" label="密码" rules={[{ required: true }]}>
              <Input.Password autoComplete="current-password" />
            </Form.Item>
            <Button type="primary" htmlType="submit" block>
              登录
            </Button>
          </Form>

          <Typography.Text type="secondary">
            没有账号？{" "}
            <Link to="/register">
              <Typography.Link style={{ cursor: "pointer" }}>去注册</Typography.Link>
            </Link>
          </Typography.Text>
        </Space>
      </Card>
    </Layout.Content>
  );
}
