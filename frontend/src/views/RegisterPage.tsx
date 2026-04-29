import { Button, Card, Form, Input, Layout, Space, Typography, message } from "antd";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { APP_DISPLAY_NAME } from "../constants/branding";
import { useAuthStore } from "../store/authStore";

type AuthResp = { access_token: string; token_type: string };

export function RegisterPage() {
  const token = useAuthStore((s) => s.token);
  const setToken = useAuthStore((s) => s.setToken);
  const navigate = useNavigate();

  if (token) {
    return <Navigate to="/" replace />;
  }

  const onRegister = async (values: { username: string; password: string }) => {
    await api.post("/auth/register", values);
    message.success("注册成功，正在登录…");
    const res = await api.post<AuthResp>("/auth/login", {
      username: values.username,
      password: values.password,
    });
    setToken(res.data.access_token);
    message.success("登录成功");
    navigate("/", { replace: true });
  };

  return (
    <Layout.Content style={{ display: "grid", placeItems: "center", padding: 24 }}>
      <Card style={{ width: "min(520px, calc(100vw - 48px))" }} variant="outlined">
        <Space direction="vertical" style={{ width: "100%" }} size="large">
          <div style={{ textAlign: "center" }}>
            <Typography.Title level={2} style={{ margin: "0 0 8px", fontWeight: 700 }}>
              {APP_DISPLAY_NAME}
            </Typography.Title>
            <Typography.Title level={4} type="secondary" style={{ margin: 0, fontWeight: 500 }}>
              注册账号
            </Typography.Title>
            <Typography.Text type="secondary" style={{ display: "block", marginTop: 8 }}>
              创建账号后将自动登录并进入系统
            </Typography.Text>
          </div>

          <Form layout="vertical" onFinish={onRegister}>
            <Form.Item name="username" label="用户名" rules={[{ required: true, min: 3 }]}>
              <Input autoComplete="username" />
            </Form.Item>
            <Form.Item name="password" label="密码" rules={[{ required: true, min: 6 }]}>
              <Input.Password autoComplete="new-password" />
            </Form.Item>
            <Button type="primary" htmlType="submit" block>
              注册
            </Button>
          </Form>

          <Typography.Text type="secondary">
            已有账号？{" "}
            <Link to="/login">
              <Typography.Link style={{ cursor: "pointer" }}>去登录</Typography.Link>
            </Link>
          </Typography.Text>
        </Space>
      </Card>
    </Layout.Content>
  );
}
