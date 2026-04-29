import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * fab: 仅可点右下角智能助手
 * send: 聊天已打开，仅可点发送（预填首句）
 * add_widget: 首次出现「添加到大屏」时仅可点该按钮
 * done: 引导结束
 */
export type OnboardingPhase = "fab" | "send" | "add_widget" | "done";

type OnboardingState = {
  phase: OnboardingPhase;
  setPhase: (p: OnboardingPhase) => void;
  /** 完成「添加到大屏」一步 */
  finishAddToDashboard: () => void;
  /** 跳过全部指引，且不再出现（持久化为 done） */
  skipOnboarding: () => void;
};

const STORAGE_KEY = "stock_chat_bi_onboarding_v1";

export const useOnboardingStore = create<OnboardingState>()(
  persist(
    (set) => ({
      phase: "fab",
      setPhase: (p) => set({ phase: p }),
      finishAddToDashboard: () => set({ phase: "done" }),
      skipOnboarding: () => set({ phase: "done" }),
    }),
    {
      name: STORAGE_KEY,
      partialize: (s) => ({ phase: s.phase }),
    },
  ),
);

export const ONBOARDING_PRESET_MESSAGE = "查询贵州茅台最近30天的行情";
