import { useCallback, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { Sidebar } from "@/components/layout/Sidebar";
import { TopBar } from "@/components/layout/TopBar";
import { ErrorBoundary } from "@/components/shared/ErrorBoundary";
import { ConnectionOverlay } from "@/components/shared/ConnectionOverlay";
import { AlertToast } from "@/components/shared/AlertToast";
import { useWebSocketBridge } from "@/hooks/useWebSocket";
import { useAlertSounds } from "@/hooks/useAlertSounds";
import { useVisibilityRefresh } from "@/hooks/useVisibilityRefresh";

const pageVariants = {
  initial: { opacity: 0, y: 8 },
  enter: { opacity: 1, y: 0, transition: { duration: 0.2, ease: "easeOut" as const } },
  exit: { opacity: 0, transition: { duration: 0.1 } },
};

export function Layout() {
  useWebSocketBridge();
  useAlertSounds();
  useVisibilityRefresh();
  const location = useLocation();
  const [navOpen, setNavOpen] = useState(false);
  const openNav = useCallback(() => setNavOpen(true), []);
  const closeNav = useCallback(() => setNavOpen(false), []);

  return (
    <div className="flex h-dvh w-screen overflow-hidden bg-bg-base">
      <ConnectionOverlay />
      <AlertToast />
      <Sidebar open={navOpen} onClose={closeNav} />
      <div className="flex flex-col flex-1 min-w-0">
        <TopBar onMenu={openNav} />
        <main className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden p-3 sm:p-4">
          {/* resetKey: a crash on one page must not follow the user to every other route */}
          <ErrorBoundary resetKey={location.pathname}>
            <AnimatePresence mode="wait">
              <motion.div
                key={location.pathname}
                variants={pageVariants}
                initial="initial"
                animate="enter"
                exit="exit"
                className="h-full min-w-0"
              >
                <Outlet />
              </motion.div>
            </AnimatePresence>
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}
