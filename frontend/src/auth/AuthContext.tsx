import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, refreshSession, setAccessToken, setSessionLostHandler } from "../api/client";
import type { Organization, Role, Session, User } from "../api/types";

interface AuthState {
  user: User;
  organization: Organization;
  role: Role;
}

interface AuthContextValue {
  session: AuthState | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (input: { email: string; password: string; display_name: string; organization_name: string }) => Promise<void>;
  logout: () => Promise<void>;
  switchOrganization: (organizationId: string) => Promise<void>;
  rename: (organization: Organization) => void;
  canWrite: boolean;
  canAdmin: boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [session, setSession] = useState<AuthState | null>(null);
  const [loading, setLoading] = useState(true);

  const apply = useCallback((value: Session) => {
    setAccessToken(value.access_token);
    setSession({ user: value.user, organization: value.organization, role: value.role });
  }, []);

  const clear = useCallback(() => {
    setAccessToken(null);
    setSession(null);
    queryClient.clear();
  }, [queryClient]);

  useEffect(() => {
    setSessionLostHandler(clear);
    refreshSession()
      .then((value) => value && apply(value))
      .finally(() => setLoading(false));
    return () => setSessionLostHandler(null);
  }, [apply, clear]);

  const value = useMemo<AuthContextValue>(
    () => ({
      session,
      loading,
      login: async (email, password) => {
        apply(await api<Session>("/v1/auth/login", { method: "POST", body: { email, password } }));
      },
      register: async (input) => {
        apply(await api<Session>("/v1/auth/register", { method: "POST", body: input }));
      },
      logout: async () => {
        await api("/v1/auth/logout", { method: "POST" }).catch(() => undefined);
        clear();
      },
      switchOrganization: async (organizationId) => {
        const next = await api<Session>("/v1/auth/switch-organization", {
          method: "POST",
          body: { organization_id: organizationId },
        });
        queryClient.clear();
        apply(next);
      },
      rename: (organization) => setSession((current) => (current ? { ...current, organization } : current)),
      canWrite: session?.role !== "viewer",
      canAdmin: session?.role === "owner" || session?.role === "admin",
    }),
    [apply, clear, loading, queryClient, session],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
