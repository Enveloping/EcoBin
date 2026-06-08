import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { LoginResponse } from '@/types';

interface AuthState {
  token: string | null;
  role: number | null;
  userId: number | null;
  tenantId: number | null;
  username: string | null;
  realName: string | null;
  /** 登录成功后写入 */
  setAuth: (data: LoginResponse) => void;
  /** 登出 / 失效：清空 */
  clear: () => void;
  isLoggedIn: () => boolean;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      token: null,
      role: null,
      userId: null,
      tenantId: null,
      username: null,
      realName: null,
      setAuth: (data) =>
        set({
          token: data.token,
          role: data.role,
          userId: data.userId,
          tenantId: data.tenantId,
          username: data.username,
          realName: data.realName,
        }),
      clear: () =>
        set({
          token: null,
          role: null,
          userId: null,
          tenantId: null,
          username: null,
          realName: null,
        }),
      isLoggedIn: () => !!get().token,
    }),
    { name: 'ecobin-auth' },
  ),
);

/** 非组件环境（axios 拦截器）读取/清理登录态 */
export const authActions = {
  getToken: () => useAuthStore.getState().token,
  clear: () => useAuthStore.getState().clear(),
};
