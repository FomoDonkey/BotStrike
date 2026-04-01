import { Outlet, useLocation } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { Sidebar } from "@/components/layout/Sidebar";
import { TopBar } from "@/components/layout/TopBar";
import { ErrorBoundary } from "@/components/shared/ErrorBoundary";
import { ConnectionOverlay } from "@/components/shared/ConnectionOverlay";
import { AlertToast } from "@/components/shared/AlertToast";
import { useWebSocketBridge } from "@/hooks/useWebSocket";
import { useAlertSounds } from "@/hooks/useAlertSounds";

const pageVariants = {
  initial: { opacity: 0, y: 8 },
  enter: { opacity: 1, y: 0, transition: { duration: 0.2, ease: "easeOut" as const } },
  exit: { opacity: 0, transition: { duration: 0.1 } },
};

export function Layout() {
  useWebSocketBridge();
  useAlertSounds();
  const location = useLocation();

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-bg-base">
      <ConnectionOverlay />
      <AlertToast />
      <Sidebar />
      <div className="flex flex-col flex-1 min-w-0">
        <TopBar />
        <main className="flex-1 overflow-auto p-4">
          <ErrorBoundary>
            <AnimatePresence mode="wait">
              <motion.div
                key={location.pathname}
                variants={pageVariants}
                initial="initial"
                animate="enter"
                exit="exit"
                className="h-full"
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
