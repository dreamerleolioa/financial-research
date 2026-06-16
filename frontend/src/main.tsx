import { GoogleOAuthProvider } from "@react-oauth/google";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import "./index.css";
import App from "./App.tsx";
import LoginPage from "./pages/LoginPage.tsx";
import LoginCallbackPage from "./pages/LoginCallbackPage.tsx";
import AnalyzePage from "./pages/AnalyzePage.tsx";
import ClosedPortfolioPage from "./pages/ClosedPortfolioPage.tsx";
import PortfolioPage from "./pages/PortfolioPage.tsx";
import DailyRadarPage from "./pages/DailyRadarPage.tsx";
import { AuthProvider, useAuth } from "./stores/auth.tsx";
import { APP_BASE_URL, GOOGLE_CLIENT_ID } from "./lib/config.ts";

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, isLoading } = useAuth();
  if (isLoading) return <div className="flex min-h-screen items-center justify-center"><div className="h-6 w-6 animate-spin rounded-full border-2 border-indigo-600 border-t-transparent" /></div>;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <GoogleOAuthProvider clientId={GOOGLE_CLIENT_ID}>
      <BrowserRouter basename={APP_BASE_URL}>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/login/callback" element={<LoginCallbackPage />} />
            <Route
              element={
                <ProtectedRoute>
                  <App />
                </ProtectedRoute>
              }
            >
              <Route index element={<Navigate to="/analyze" replace />} />
              <Route path="/analyze" element={<AnalyzePage />} />
              <Route path="/portfolio" element={<PortfolioPage onNavigateAnalyze={() => { }} />} />
              <Route path="/portfolio/closed" element={<ClosedPortfolioPage />} />
              <Route path="/daily-radar" element={<DailyRadarPage />} />
              <Route path="*" element={<Navigate to="/analyze" replace />} />
            </Route>
          </Routes>
        </AuthProvider>
      </BrowserRouter>
    </GoogleOAuthProvider>
  </StrictMode>,
);
