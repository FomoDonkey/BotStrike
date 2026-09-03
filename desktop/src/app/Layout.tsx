import { useEffect, useRef } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { TopNav } from "@/components/layout/TopNav";
import { FooterBar } from "@/components/layout/FooterBar";
import { MobileTabBar } from "@/components/layout/MobileTabBar";
import { ActivityDrawer } from "@/components/layout/ActivityDrawer";
import { ErrorBoundary } from "@/components/shared/ErrorBoundary";
import { ConnectionOverlay } from "@/components/shared/ConnectionOverlay";
import { AlertToast } from "@/components/shared/AlertToast";
import { useWebSocketBridge } from "@/hooks/useWebSocket";
import { useAlertSounds } from "@/hooks/useAlertSounds";
import { useVisibilityRefresh } from "@/hooks/useVisibilityRefresh";

/**
 * App shell (spec §2): 56 px top nav · scrolling main · 32 px footer bar (≥ lg) / 56 px bottom
 * tab bar (< lg). No route fade: a half-opaque page is exactly what the contrast audit flags.
 */
export function Layout() {
  useWebSocketBridge();
  useAlertSounds();
  useVisibilityRefresh();
  const location = useLocation();
  const mainRef = useRef<HTMLElement>(null);

  // <main> is shared by every route: without this, Trade's scroll position carried over to Portfolio.
  useEffect(() => {
    mainRef.current?.scrollTo({ top: 0 });
  }, [location.pathname]);

  return (
    <div className="flex flex-col h-dvh w-screen overflow-hidden bg-bg">
      <ConnectionOverlay />
      <AlertToast />
      <TopNav />
      <main ref={mainRef} className="flex-1 min-h-0 min-w-0 overflow-y-auto overflow-x-hidden pb-14 lg:pb-0">
        {/* resetKey: a crash on one page must not follow the user to every other route */}
        <ErrorBoundary resetKey={location.pathname}>
          <Outlet />
        </ErrorBoundary>
      </main>
      <FooterBar className="hidden lg:flex" />
      <MobileTabBar className="lg:hidden" />
      <ActivityDrawer />
    </div>
  );
}
