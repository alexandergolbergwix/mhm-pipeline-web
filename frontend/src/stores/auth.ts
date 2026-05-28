import { create } from "zustand";

import { api, ApiError } from "@/api/client";

export interface AuthUser {
  id: string;
  email: string;
  name: string;
}

interface AuthState {
  user: AuthUser | null;
  loading: boolean;     // initial /me check still in flight
  error: string | null;
  // — actions —
  bootstrap: () => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

export const useAuth = create<AuthState>((set) => ({
  user: null,
  loading: true,
  error: null,

  async bootstrap() {
    try {
      const me = await api.get<AuthUser>("/auth/me");
      set({ user: me, loading: false, error: null });
    } catch (err) {
      // 401 is expected when not logged in.
      const msg = err instanceof ApiError && err.status === 401 ? null : String(err);
      set({ user: null, loading: false, error: msg });
    }
  },

  async login(email, password) {
    set({ error: null });
    try {
      const me = await api.post<AuthUser>("/auth/login", { email, password });
      set({ user: me, error: null });
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : "Login failed";
      set({ error: msg });
      throw err;
    }
  },

  async logout() {
    try {
      await api.post("/auth/logout");
    } finally {
      set({ user: null, error: null });
    }
  },
}));
