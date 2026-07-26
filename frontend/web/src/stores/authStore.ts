import { create } from 'zustand';
import type { LoginResponse, WebLoginDomain } from '@/types';

export type AuthStatus = 'checking' | 'authenticated' | 'anonymous';

interface AuthState {
  status: AuthStatus;
  session: LoginResponse | null;
  domain: WebLoginDomain | null;
  setChecking: () => void;
  setSession: (session: LoginResponse, domain: WebLoginDomain) => void;
  clear: () => void;
  hasCapability: (capability: string) => boolean;
}

/**
 * Web credentials never enter this store. The only credential is the server-set
 * Secure + HttpOnly Cookie; this in-memory state is a reloadable UI projection.
 */
export const useAuthStore = create<AuthState>()((set, get) => ({
  status: 'checking',
  session: null,
  domain: null,
  setChecking: () => set({ status: 'checking' }),
  setSession: (session, domain) =>
    set({ status: 'authenticated', session, domain }),
  clear: () => set({ status: 'anonymous', session: null, domain: null }),
  hasCapability: (capability) =>
    get().session?.capabilities.includes(capability) ?? false,
}));

export const authActions = {
  clear: () => useAuthStore.getState().clear(),
};
