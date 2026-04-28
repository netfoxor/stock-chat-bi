import { Button, Card, Form, Input, Layout, Space, Typography, message } from "antd";
import { api } from "../api/client";
import { useAuthStore } from "../store/authStore";

type AuthResp = { access_token: string; token_type: string };

export function LoginPage() {
  const setToken = useAuthStore((s) => s.setToken);

  const onLogin = async (values: any) => {
    const res = await api.post<AuthResp>("/auth/login", values);
    setToken(res.data.access_token);
    message.success("登录成功");
    window.location.href = "/";
  };

  const onRegister = async (values: any) => {
    await api.post("/auth/register", values);
    message.success("注册成功，请登录");
  };

  return (
    <Layout.Content style={{ display: "grid", placeItems: "center", padding: 24 }}>
      <Card style={{ width: 420 }} variant="outlined">
        <Space direction="vertical" style={{ width: "100%" }} size="large">
          <div>
            <Typography.Title level={3} style={{ margin: 0 }}>
              Stock Chat BI
            </Typography.Title>
            <Typography.Text type="secondary">注册/登录后进入大屏与聊天</Typography.Text>
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

          <Form layout="vertical" onFinish={onRegister}>
            <Typography.Text strong>没有账号？先注册</Typography.Text>
            <Form.Item name="username" label="用户名" rules={[{ required: true, min: 3 }]}>
              <Input autoComplete="username" />
            </Form.Item>
            <Form.Item name="password" label="密码" rules={[{ required: true, min: 6 }]}>
              <Input.Password autoComplete="new-password" />
            </Form.Item>
            <Button htmlType="submit" block>
              注册
            </Button>
          </Form>
        </Space>
      </Card>
    </Layout.Content>
  );
}

