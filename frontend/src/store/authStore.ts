import { create } from "zustand";

type AuthState = {
  token: string | null;
  setToken: (token: string | null) => void;
  logout: () => void;
};

const LS_KEY = "stock_chat_bi_token";

export const useAuthStore = create<AuthState>((set) => ({
  token: localStorage.getItem(LS_KEY),
  setToken: (token) => {
    if (token) localStorage.setItem(LS_KEY, token);
    else localStorage.removeItem(LS_KEY);
    set({ token });
  },
  logout: () => {
    localStorage.removeItem(LS_KEY);
    set({ token: null });
  },
}));

