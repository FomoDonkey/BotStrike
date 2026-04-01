import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter, Routes, Route } from "react-router-dom";
import { Layout } from "@/app/Layout";
import { DashboardPage } from "@/pages/dashboard/DashboardPage";
import { TradingPage } from "@/pages/trading/TradingPage";
import { PerformancePage } from "@/pages/performance/PerformancePage";
import { OrderFlowPage } from "@/pages/orderflow/OrderFlowPage";
import { StrategiesPage } from "@/pages/strategies/StrategiesPage";
import { RiskPage } from "@/pages/risk/RiskPage";
import { BacktestPage } from "@/pages/backtest/BacktestPage";
import { DataPage } from "@/pages/data/DataPage";
import { SettingsPage } from "@/pages/settings/SettingsPage";
import { SystemPage } from "@/pages/system/SystemPage";
import "./index.css";

// Safe theme init — wrapped in try-catch for WebView compat
try {
  const { initTheme } = await import("@/stores/themeStore");
  initTheme();
} catch {
  // Default CSS theme applies
}

const root = document.getElementById("root");
if (root) {
  ReactDOM.createRoot(root).render(
    <React.StrictMode>
      <HashRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/trading" element={<TradingPage />} />
            <Route path="/performance" element={<PerformancePage />} />
            <Route path="/orderflow" element={<OrderFlowPage />} />
            <Route path="/strategies" element={<StrategiesPage />} />
            <Route path="/risk" element={<RiskPage />} />
            <Route path="/backtest" element={<BacktestPage />} />
            <Route path="/data" element={<DataPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/system" element={<SystemPage />} />
          </Route>
        </Routes>
      </HashRouter>
    </React.StrictMode>
  );
}
