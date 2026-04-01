import { Outlet } from "react-router-dom";
import { Sidebar } from "@/components/layout/Sidebar";
import { TopBar } from "@/components/layout/TopBar";
import { ErrorBoundary } from "@/components/shared/ErrorBoundary";
import { ConnectionOverlay } from "@/components/shared/ConnectionOverlay";
import { useWebSocketBridge } from "@/hooks/useWebSocket";

export function Layout() {
  useWebSocketBridge();

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-bg-base">
      <ConnectionOverlay />
      <Sidebar />
      <div className="flex flex-col flex-1 min-w-0">
        <TopBar />
        <main className="flex-1 overflow-auto p-4">
          <ErrorBoundary>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}
