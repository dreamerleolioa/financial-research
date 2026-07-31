import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { markPortfolioMutationStarted } from "../features/portfolio/mutationCoordinator";
import { clearPriceRefreshOverlay } from "../features/portfolio/priceRefreshOverlay";
import { requestJson } from "../lib/apiClient";
import { AUTH_TOKEN_STORAGE_KEY, clearToken, getToken, setToken } from "../lib/auth";

interface User {
  id: number;
  email: string;
  name: string | null;
  avatar_url: string | null;
}

interface AuthState {
  user: User | null;
  token: string | null;
  isLoading: boolean;
}

interface AuthContextValue extends AuthState {
  loginWithGoogleToken: (idToken: string) => Promise<void>;
  loginWithGoogleCode: (code: string, redirectUri: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const verificationSequenceRef = useRef(0);
  const [state, setState] = useState<AuthState>({
    user: null,
    token: getToken(),
    isLoading: true,
  });

  const clearUserScopedCache = useCallback(() => {
    markPortfolioMutationStarted();
    clearPriceRefreshOverlay(queryClient);
    void queryClient.cancelQueries();
    queryClient.clear();
  }, [queryClient]);

  const verifyStoredToken = useCallback(
    async (token: string) => {
      const verificationSequence = ++verificationSequenceRef.current;
      setState({ user: null, token, isLoading: true });
      try {
        const user = await requestJson<User>("/auth/me");
        if (
          verificationSequenceRef.current !== verificationSequence ||
          getToken() !== token
        ) {
          return;
        }
        setState({ user, token, isLoading: false });
      } catch {
        if (
          verificationSequenceRef.current !== verificationSequence ||
          getToken() !== token
        ) {
          return;
        }
        clearUserScopedCache();
        clearToken();
        setState({ user: null, token: null, isLoading: false });
      }
    },
    [clearUserScopedCache],
  );

  useEffect(() => {
    const token = getToken();
    if (token) {
      void verifyStoredToken(token);
    } else {
      verificationSequenceRef.current += 1;
      setState({ user: null, token: null, isLoading: false });
    }

    const handleStorage = (event: StorageEvent) => {
      if (event.key !== AUTH_TOKEN_STORAGE_KEY) return;
      clearUserScopedCache();
      if (event.newValue) {
        void verifyStoredToken(event.newValue);
      } else {
        verificationSequenceRef.current += 1;
        setState({ user: null, token: null, isLoading: false });
      }
    };
    window.addEventListener("storage", handleStorage);
    return () => window.removeEventListener("storage", handleStorage);
  }, [clearUserScopedCache, verifyStoredToken]);

  const loginWithGoogleToken = useCallback(
    async (idToken: string) => {
      const verificationSequence = ++verificationSequenceRef.current;
      const data = await requestJson<{ access_token: string; user: User }>("/auth/google", {
        method: "POST",
        auth: false,
        body: { id_token: idToken },
      });
      if (verificationSequenceRef.current !== verificationSequence) {
        throw new Error("登入狀態已在其他分頁變更，請重新登入");
      }
      clearUserScopedCache();
      setToken(data.access_token);
      setState({ user: data.user, token: data.access_token, isLoading: false });
    },
    [clearUserScopedCache],
  );

  const loginWithGoogleCode = useCallback(
    async (code: string, redirectUri: string) => {
      const verificationSequence = ++verificationSequenceRef.current;
      const data = await requestJson<{ access_token: string; user: User }>("/auth/google/code", {
        method: "POST",
        auth: false,
        body: { code, redirect_uri: redirectUri },
      });
      if (verificationSequenceRef.current !== verificationSequence) {
        throw new Error("登入狀態已在其他分頁變更，請重新登入");
      }
      clearUserScopedCache();
      setToken(data.access_token);
      setState({ user: data.user, token: data.access_token, isLoading: false });
    },
    [clearUserScopedCache],
  );

  const logout = useCallback(() => {
    verificationSequenceRef.current += 1;
    clearUserScopedCache();
    clearToken();
    setState({ user: null, token: null, isLoading: false });
  }, [clearUserScopedCache]);

  return (
    <AuthContext.Provider value={{ ...state, loginWithGoogleToken, loginWithGoogleCode, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
