import { create } from 'zustand';
import type { LoginResponse, WebLoginDomain } from '@/types';

export type AuthStatus =
  | 'checking'
  | 'authenticated'
  | 'anonymous'
  | 'unavailable';

export interface AuthBootstrapIssue {
  message: string;
  requestId?: string;
}

interface AuthState {
  status: AuthStatus;
  session: LoginResponse | null;
  domain: WebLoginDomain | null;
  bootstrapIssue: AuthBootstrapIssue | null;
  setChecking: () => void;
  setSession: (session: LoginResponse, domain: WebLoginDomain) => void;
  setUnavailable: (issue: AuthBootstrapIssue) => void;
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
  bootstrapIssue: null,
  setChecking: () => set({ status: 'checking', bootstrapIssue: null }),
  setSession: (session, domain) =>
    set({
      status: 'authenticated',
      session,
      domain,
      bootstrapIssue: null,
    }),
  setUnavailable: (bootstrapIssue) =>
    set({
      status: 'unavailable',
      session: null,
      domain: null,
      bootstrapIssue,
    }),
  clear: () => set({
    status: 'anonymous',
    session: null,
    domain: null,
    bootstrapIssue: null,
  }),
  hasCapability: (capability) =>
    get().session?.capabilities.includes(capability) ?? false,
}));

export const authActions = {
  clear: () => useAuthStore.getState().clear(),
};
