import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AuthenticationShell } from "../components/auth/AuthenticationShell";
import { appPath } from "../lib/config";
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
      setError("未收到 Google 授權碼，請返回登入頁後再試一次。");
      return;
    }

    loginWithGoogleCode(code, `${window.location.origin}${appPath("login/callback")}`)
      .then(() => navigate("/", { replace: true }))
      .catch(() => setError("目前無法完成登入，請返回登入頁後再試一次。"));
  }, [loginWithGoogleCode, navigate]);

  if (error) {
    return (
      <AuthenticationShell
        eyebrow="Sign-in interrupted"
        title="登入尚未完成"
        description="Google 授權流程沒有成功完成，你的研究資料沒有受到影響。"
      >
        <div
          role="alert"
          className="rounded-[10px] border border-red-200 bg-red-50 px-4 py-3 text-sm leading-relaxed text-red-700 dark:border-red-900 dark:bg-red-950/45 dark:text-red-300"
        >
          {error}
        </div>
        <Link to="/login" className="ui-button-primary mt-4">
          返回登入頁
        </Link>
      </AuthenticationShell>
    );
  }

  return (
    <AuthenticationShell
      eyebrow="Completing sign-in"
      title="正在完成登入"
      description="正在確認 Google 授權並載入你的個人研究工作區。"
    >
      <div className="flex items-center gap-3 border-y border-border py-5" role="status" aria-live="polite">
        <span className="h-6 w-6 animate-spin rounded-full border-2 border-border border-t-accent" aria-hidden="true" />
        <div>
          <p className="text-sm font-medium text-text-primary">驗證授權資料</p>
          <p className="mt-1 text-xs text-text-faint">完成後會自動進入個股分析頁。</p>
        </div>
      </div>
    </AuthenticationShell>
  );
}
