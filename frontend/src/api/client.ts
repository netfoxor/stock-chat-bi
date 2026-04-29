import axios from "axios";
import { useAuthStore } from "../store/authStore";

/** 与同域部署一致：Docker/Nginx 下为 `/api`；本地 `pnpm dev` 由 vite 代理到后端 */
export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "/api",
});

api.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token;
  if (token) {
    config.headers = config.headers ?? {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

