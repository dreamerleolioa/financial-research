import { createContext, useCallback, useContext, useEffect, useState } from "react";
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
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AuthState>({
    user: null,
    token: getToken(),
    isLoading: true,
  });

  const apiUrl = import.meta.env.VITE_API_URL as string;

  // On mount, verify existing token
  useEffect(() => {
    const token = getToken();
    if (!token) {
      setState({ user: null, token: null, isLoading: false });
      return;
    }
    fetch(`${apiUrl}/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((res) => {
        if (!res.ok) throw new Error("Unauthorized");
        return res.json() as Promise<User>;
      })
      .then((user) => setState({ user, token, isLoading: false }))
      .catch(() => {
        clearToken();
        setState({ user: null, token: null, isLoading: false });
      });
  }, [apiUrl]);

  const loginWithGoogleToken = useCallback(
    async (idToken: string) => {
      const res = await fetch(`${apiUrl}/auth/google`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id_token: idToken }),
      });
      if (!res.ok) throw new Error("Google login failed");
      const data = await res.json() as { access_token: string; user: User };
      setToken(data.access_token);
      setState({ user: data.user, token: data.access_token, isLoading: false });
    },
    [apiUrl],
  );

  const logout = useCallback(() => {
    clearToken();
    setState({ user: null, token: null, isLoading: false });
  }, []);

  return (
    <AuthContext.Provider value={{ ...state, loginWithGoogleToken, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
