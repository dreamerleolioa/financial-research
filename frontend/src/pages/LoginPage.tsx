import { GoogleLogin } from "@react-oauth/google";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../stores/auth";

export default function LoginPage() {
  const { loginWithGoogleToken } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50">
      <div className="w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-8 shadow-sm">
        <h1 className="mb-2 text-xl font-semibold text-slate-900">個股分析儀表板</h1>
        <p className="mb-8 text-sm text-slate-500">請以 Google 帳號登入以繼續。</p>
        {error && (
          <p className="mb-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600">{error}</p>
        )}
        <GoogleLogin
          onSuccess={async (credentialResponse) => {
            if (!credentialResponse.credential) return;
            setError(null);
            try {
              await loginWithGoogleToken(credentialResponse.credential);
              navigate("/");
            } catch {
              setError("登入失敗，請稍後再試。");
            }
          }}
          onError={() => {
            setError("Google 登入被拒絕，請確認瀏覽器設定。");
          }}
        />
      </div>
    </main>
  );
}
