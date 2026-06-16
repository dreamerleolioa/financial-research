import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { requestJson } from "../lib/apiClient";
import { clearToken, getToken, setToken } from "../lib/auth";

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
  const [state, setState] = useState<AuthState>({
    user: null,
    token: getToken(),
    isLoading: true,
  });

  // On mount, verify existing token
  useEffect(() => {
    const token = getToken();
    if (!token) {
      setState({ user: null, token: null, isLoading: false });
      return;
    }
    requestJson<User>("/auth/me")
      .then((user) => setState({ user, token, isLoading: false }))
      .catch(() => {
        clearToken();
        setState({ user: null, token: null, isLoading: false });
      });
  }, []);

  const loginWithGoogleToken = useCallback(async (idToken: string) => {
    const data = await requestJson<{ access_token: string; user: User }>("/auth/google", {
      method: "POST",
      auth: false,
      body: { id_token: idToken },
    });
    setToken(data.access_token);
    setState({ user: data.user, token: data.access_token, isLoading: false });
  }, []);

  const loginWithGoogleCode = useCallback(async (code: string, redirectUri: string) => {
    const data = await requestJson<{ access_token: string; user: User }>("/auth/google/code", {
      method: "POST",
      auth: false,
      body: { code, redirect_uri: redirectUri },
    });
    setToken(data.access_token);
    setState({ user: data.user, token: data.access_token, isLoading: false });
  }, []);

  const logout = useCallback(() => {
    clearToken();
    setState({ user: null, token: null, isLoading: false });
  }, []);

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
