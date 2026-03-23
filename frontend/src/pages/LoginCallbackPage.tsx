import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../stores/auth";

export default function LoginCallbackPage() {
  const { loginWithGoogleCode } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const handled = useRef(false);

  useEffect(() => {
    if (handled.current) return;
    handled.current = true;

    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");

    if (!code) {
      setError("未收到 Google 授權碼，請重新登入。");
      return;
    }

    loginWithGoogleCode(code, `${window.location.origin}${import.meta.env.BASE_URL}login/callback`)
      .then(() => navigate("/", { replace: true }))
      .catch(() => setError("登入失敗，請稍後再試。"));
  }, [loginWithGoogleCode, navigate]);

  if (error) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-50 dark:bg-slate-950">
        <div className="w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-8 shadow-sm dark:border-slate-700 dark:bg-slate-900">
          <p className="mb-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600 dark:bg-red-950 dark:text-red-400">{error}</p>
          <a href="/login" className="text-sm text-indigo-600 hover:underline dark:text-indigo-400">
            返回登入頁
          </a>
        </div>
      </main>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 dark:bg-slate-950">
      <div className="h-6 w-6 animate-spin rounded-full border-2 border-indigo-600 border-t-transparent dark:border-indigo-400" />
    </div>
  );
}
