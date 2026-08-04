import { useGoogleLogin } from "@react-oauth/google";
import { useState } from "react";
import { Navigate } from "react-router-dom";
import { AuthenticationShell } from "../components/auth/AuthenticationShell";
import { appPath } from "../lib/config";
import { createGoogleOAuthState, storeGoogleOAuthState } from "../lib/googleOAuthState";
import { useAuth } from "../stores/auth";

function GoogleIcon() {
  return (
    <svg className="h-5 w-5" viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"
        fill="#4285F4"
      />
      <path
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
        fill="#34A853"
      />
      <path
        d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
        fill="#FBBC05"
      />
      <path
        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
        fill="#EA4335"
      />
    </svg>
  );
}

export default function LoginPage() {
  const { user, isLoading } = useAuth();
  const [oauthState] = useState(createGoogleOAuthState);

  const login = useGoogleLogin({
    flow: "auth-code",
    ux_mode: "redirect",
    redirect_uri: `${window.location.origin}${appPath("login/callback")}`,
    state: oauthState,
  });

  if (user) return <Navigate to="/" replace />;

  return (
    <AuthenticationShell
      eyebrow="Private research workspace"
      title="回到你的個股研究工作區"
      description="使用 Google 帳號登入，繼續整理關注標的、持股風險與每日盤後觀察。"
    >
      <button
        type="button"
        onClick={() => {
          storeGoogleOAuthState(oauthState);
          login();
        }}
        disabled={isLoading}
        className="ui-button-secondary w-full justify-center bg-surface-raised py-3 text-text-primary shadow-panel"
      >
        {isLoading ? (
          <>
            <span
              className="h-5 w-5 animate-spin rounded-full border-2 border-border border-t-accent"
              aria-hidden="true"
            />
            驗證登入狀態
          </>
        ) : (
          <>
            <GoogleIcon />
            使用 Google 帳號登入
          </>
        )}
      </button>

      <div className="mt-5 border-t border-border-subtle pt-5">
        <p className="text-xs leading-relaxed text-text-faint">
          登入只用於辨識個人研究資料。系統不會代表你執行交易，也不會將觀察清單視為投資建議。
        </p>
      </div>
    </AuthenticationShell>
  );
}
