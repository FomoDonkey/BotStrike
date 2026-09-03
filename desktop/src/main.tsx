import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter, Routes, Route, Navigate } from "react-router-dom";
import { Layout } from "@/app/Layout";
import { TradingPage } from "@/pages/trading/TradingPage";
import { PortfolioPage } from "@/pages/portfolio/PortfolioPage";
import { StrategiesPage } from "@/pages/strategies/StrategiesPage";
import { RiskPage } from "@/pages/risk/RiskPage";
import { BacktestPage } from "@/pages/backtest/BacktestPage";
import { DataPage } from "@/pages/data/DataPage";
import { SettingsPage } from "@/pages/settings/SettingsPage";
import { SystemPage } from "@/pages/system/SystemPage";
import { initTheme } from "@/stores/themeStore";
import { initUiDisplay } from "@/stores/uiStore";
import "./index.css";

// Safe theme init — wrapped in try-catch for WebView compat.
try {
  initTheme();
  initUiDisplay();
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
            <Route path="/" element={<Navigate to="/trading" replace />} />
            <Route path="/trading" element={<TradingPage />} />
            <Route path="/portfolio" element={<PortfolioPage />} />
            {/* v2.15 routes → Portfolio replaces Dashboard + Performance; Order Flow lives in Trade → Details */}
            <Route path="/dashboard" element={<Navigate to="/portfolio" replace />} />
            <Route path="/performance" element={<Navigate to="/portfolio" replace />} />
            <Route path="/orderflow" element={<Navigate to="/trading" replace />} />
            <Route path="/strategies" element={<StrategiesPage />} />
            <Route path="/risk" element={<RiskPage />} />
            <Route path="/backtest" element={<BacktestPage />} />
            <Route path="/data" element={<DataPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/system" element={<SystemPage />} />
            <Route path="*" element={<Navigate to="/trading" replace />} />
          </Route>
        </Routes>
      </HashRouter>
    </React.StrictMode>
  );
}
