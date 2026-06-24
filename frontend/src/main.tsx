import { GoogleOAuthProvider } from "@react-oauth/google";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, StrictMode, Suspense, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import "./index.css";
import App from "./App.tsx";
import LoginPage from "./pages/LoginPage.tsx";
import LoginCallbackPage from "./pages/LoginCallbackPage.tsx";
import { AuthProvider, useAuth } from "./stores/auth.tsx";
import { APP_BASE_URL, GOOGLE_CLIENT_ID } from "./lib/config.ts";

const AnalyzePage = lazy(() => import("./pages/AnalyzePage.tsx"));
const ClosedPortfolioPage = lazy(() => import("./pages/ClosedPortfolioPage.tsx"));
const DailyRadarPage = lazy(() => import("./pages/DailyRadarPage.tsx"));
const PortfolioPage = lazy(() => import("./pages/PortfolioPage.tsx"));
const WatchlistPage = lazy(() => import("./pages/WatchlistPage.tsx"));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: false,
    },
  },
});

function LoadingSpinner({ minHeightClass = "min-h-screen" }: { minHeightClass?: string }) {
  return (
    <div className={`flex ${minHeightClass} items-center justify-center`}>
      <div className="h-6 w-6 animate-spin rounded-full border-2 border-indigo-600 border-t-transparent" />
    </div>
  );
}

function ProtectedRoute({ children }: { children: ReactNode }) {
  const { user, isLoading } = useAuth();
  if (isLoading) return <LoadingSpinner />;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function LazyRoute({ children }: { children: ReactNode }) {
  return <Suspense fallback={<LoadingSpinner minHeightClass="min-h-[40vh]" />}>{children}</Suspense>;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <GoogleOAuthProvider clientId={GOOGLE_CLIENT_ID}>
      <QueryClientProvider client={queryClient}>
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
                <Route
                  path="/analyze"
                  element={
                    <LazyRoute>
                      <AnalyzePage />
                    </LazyRoute>
                  }
                />
                <Route
                  path="/watchlist"
                  element={
                    <LazyRoute>
                      <WatchlistPage />
                    </LazyRoute>
                  }
                />
                <Route
                  path="/portfolio"
                  element={
                    <LazyRoute>
                      <PortfolioPage onNavigateAnalyze={() => {}} />
                    </LazyRoute>
                  }
                />
                <Route
                  path="/portfolio/closed"
                  element={
                    <LazyRoute>
                      <ClosedPortfolioPage />
                    </LazyRoute>
                  }
                />
                <Route
                  path="/daily-radar"
                  element={
                    <LazyRoute>
                      <DailyRadarPage />
                    </LazyRoute>
                  }
                />
                <Route path="*" element={<Navigate to="/analyze" replace />} />
              </Route>
            </Routes>
          </AuthProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </GoogleOAuthProvider>
  </StrictMode>,
);
