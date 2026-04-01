"""
Visualización en tiempo real del backtesting en terminal.

Usa rich para mostrar:
  - Barra de progreso con ETA
  - Equity curve ASCII
  - Métricas en vivo (PnL, Sharpe, WR, drawdown)
  - Posiciones abiertas
  - Trades recientes
  - Régimen de mercado y microestructura
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text

import numpy as np


class BacktestLiveDisplay:
    """Muestra el progreso del backtest en tiempo real en la terminal."""

    def __init__(self, symbol: str, total_bars: int, refresh_rate: int = 4):
        self.symbol = symbol
        self.total_bars = total_bars
        self.console = Console()
        self.refresh_rate = refresh_rate

        # Estado
        self._start_time = time.time()
        self._last_update = 0
        self._update_interval = 1.0 / refresh_rate
        self._equity_history: List[float] = []
        self._price_history: List[float] = []
        self._last_trades: List[dict] = []
        self._trade_count_at_last_update = 0

        # Progress bar
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=40),
            MofNCompleteColumn(),
            TextColumn("[cyan]{task.percentage:>5.1f}%"),
            TimeElapsedColumn(),
            TextColumn("ETA:"),
            TimeRemainingColumn(),
        )
        self._task_id = self._progress.add_task(
            f"Backtest {symbol}", total=total_bars
        )

        # Live display
        self._live = Live(
            self._build_display(0, 0, 100000, 100000, {}, None, None, None),
            console=self.console,
            refresh_per_second=refresh_rate,
            transient=False,
        )

    def start(self) -> None:
        self._start_time = time.time()
        self._live.start()

    def stop(self) -> None:
        self._progress.update(self._task_id, completed=self.total_bars)
        self._live.stop()

    def update(
        self,
        bar_index: int,
        total_bars: int,
        timestamp: float,
        price: float,
        equity: float,
        initial_capital: float,
        positions: dict,
        result: Any,
        regime: Any,
        micro_snap: Any,
    ) -> None:
        """Callback para cada barra del backtester."""
        now = time.time()
        if now - self._last_update < self._update_interval:
            return
        self._last_update = now

        self._equity_history.append(equity)
        self._price_history.append(price)

        # Capturar trades nuevos
        if result and result.trades:
            new_count = len(result.trades)
            if new_count > self._trade_count_at_last_update:
                new_trades = result.trades[self._trade_count_at_last_update:]
                self._last_trades = (self._last_trades + new_trades)[-8:]
                self._trade_count_at_last_update = new_count

        self._progress.update(self._task_id, completed=bar_index)

        display = self._build_display(
            bar_index, timestamp, price, equity,
            positions, result, regime, micro_snap,
            initial_capital=initial_capital,
        )
        self._live.update(display)

    def _build_display(
        self,
        bar_index: int,
        timestamp: float,
        price: float,
        equity: float,
        positions: dict,
        result: Any,
        regime: Any,
        micro_snap: Any,
        initial_capital: float = 100000,
    ) -> Group:
        """Construye el layout completo del display."""
        panels = []

        # 1. Progress bar
        panels.append(self._progress)

        # 2. Métricas principales
        metrics_table = self._build_metrics_table(
            bar_index, timestamp, price, equity, result, initial_capital
        )
        panels.append(Panel(metrics_table, title="[bold white]Metricas", border_style="blue"))

        # 3. Equity curve ASCII
        if len(self._equity_history) > 10:
            chart = self._build_equity_chart(initial_capital)
            panels.append(Panel(chart, title="[bold white]Equity Curve", border_style="green"))

        # 4. Posiciones abiertas + Régimen + Microestructura
        side_panels = Table.grid(expand=True)
        side_panels.add_column(ratio=1)
        side_panels.add_column(ratio=1)

        pos_panel = Panel(
            self._build_positions_table(positions, price),
            title="[bold white]Posiciones",
            border_style="yellow",
        )
        regime_panel = Panel(
            self._build_regime_micro(regime, micro_snap),
            title="[bold white]Mercado",
            border_style="magenta",
        )
        side_panels.add_row(pos_panel, regime_panel)
        panels.append(side_panels)

        # 5. Trades recientes
        if self._last_trades:
            panels.append(Panel(
                self._build_trades_table(),
                title="[bold white]Trades Recientes",
                border_style="cyan",
            ))

        return Group(*panels)

    def _build_metrics_table(
        self, bar_index, timestamp, price, equity, result, initial_capital
    ) -> Table:
        table = Table.grid(expand=True, padding=(0, 2))
        table.add_column(justify="right", style="bold")
        table.add_column(justify="left")
        table.add_column(justify="right", style="bold")
        table.add_column(justify="left")
        table.add_column(justify="right", style="bold")
        table.add_column(justify="left")

        pnl = equity - initial_capital
        pnl_pct = pnl / initial_capital * 100
        pnl_color = "green" if pnl >= 0 else "red"

        # Win rate
        total_trades = len(result.trades) if result and result.trades else 0
        wins = sum(1 for t in (result.trades if result and result.trades else []) if t.get("pnl", 0) > 0)
        wr = wins / total_trades if total_trades > 0 else 0

        # Max drawdown
        max_dd = 0
        if self._equity_history:
            eq = np.array(self._equity_history)
            peak = np.maximum.accumulate(eq)
            dd = (peak - eq) / peak
            max_dd = float(np.max(dd)) if len(dd) > 0 else 0

        # Sharpe (aproximado)
        sharpe = 0
        if len(self._equity_history) > 100:
            returns = np.diff(self._equity_history) / np.array(self._equity_history[:-1])
            if np.std(returns) > 0:
                sharpe = np.mean(returns) / np.std(returns) * np.sqrt(525600)  # anualizado 1m

        # Timestamp legible
        ts_str = ""
        if timestamp > 1e12:
            ts_str = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        elif timestamp > 1e9:
            ts_str = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

        # Velocidad
        elapsed = time.time() - self._start_time
        bars_per_sec = bar_index / elapsed if elapsed > 0 else 0

        table.add_row(
            "Precio:", f"${price:,.2f}",
            "Equity:", f"[{pnl_color}]${equity:,.2f}[/]",
            "PnL:", f"[{pnl_color}]${pnl:+,.2f} ({pnl_pct:+.2f}%)[/]",
        )
        table.add_row(
            "Fecha:", ts_str,
            "Trades:", str(total_trades),
            "Win Rate:", f"{wr:.1%}" if total_trades > 0 else "N/A",
        )
        table.add_row(
            "Velocidad:", f"{bars_per_sec:,.0f} bars/s",
            "Sharpe:", f"{sharpe:.2f}",
            "Max DD:", f"[red]{max_dd:.2%}[/]" if max_dd > 0.01 else f"{max_dd:.2%}",
        )
        table.add_row(
            "Signals:", str(result.signals_generated if result else 0),
            "Ejecutadas:", str(result.signals_executed if result else 0),
            "Profit Factor:", self._calc_pf(result),
        )
        return table

    def _calc_pf(self, result) -> str:
        if not result or not result.trades:
            return "N/A"
        gross_profit = sum(t["pnl"] for t in result.trades if t.get("pnl", 0) > 0)
        gross_loss = abs(sum(t["pnl"] for t in result.trades if t.get("pnl", 0) < 0))
        if gross_loss == 0:
            return "inf" if gross_profit > 0 else "N/A"
        pf = gross_profit / gross_loss
        color = "green" if pf > 1.0 else "red"
        return f"[{color}]{pf:.2f}[/]"

    def _build_equity_chart(self, initial_capital: float) -> Text:
        """Genera un mini equity chart ASCII."""
        # Tomar muestras equidistantes
        eq = self._equity_history
        width = 70
        height = 12

        if len(eq) <= width:
            sampled = eq
        else:
            step = len(eq) / width
            sampled = [eq[int(i * step)] for i in range(width)]

        if not sampled:
            return Text("Sin datos")

        min_eq = min(sampled)
        max_eq = max(sampled)
        eq_range = max_eq - min_eq if max_eq != min_eq else 1

        # Caracteres de bloque para suavidad
        blocks = [" ", "\u2581", "\u2582", "\u2583", "\u2584", "\u2585", "\u2586", "\u2587", "\u2588"]

        chart_text = Text()

        # Línea de referencia (capital inicial)
        ref_y = int((initial_capital - min_eq) / eq_range * (height - 1))

        for row in range(height - 1, -1, -1):
            line = ""
            for col, val in enumerate(sampled):
                y = (val - min_eq) / eq_range * (height - 1)
                if int(y) > row:
                    line += "\u2588"
                elif int(y) == row:
                    frac = y - int(y)
                    line += blocks[int(frac * 8)]
                else:
                    line += " "
            # Color de la línea
            if row == ref_y:
                chart_text.append(line + "\n", style="dim white")
            else:
                chart_text.append(line + "\n", style="green" if row > ref_y else "red")

        # Leyenda
        pnl_final = sampled[-1] - initial_capital
        pnl_color = "green" if pnl_final >= 0 else "red"
        chart_text.append(
            f" ${min_eq:,.0f}{'':>20s}${max_eq:,.0f}{'':>10s}"
            f"Final: ${sampled[-1]:,.0f} ({pnl_final:+,.0f})",
            style=pnl_color,
        )
        return chart_text

    def _build_positions_table(self, positions: dict, price: float) -> Table:
        table = Table(expand=True, show_header=True, show_lines=False)
        table.add_column("Estrategia", style="cyan")
        table.add_column("Lado", justify="center")
        table.add_column("Entrada", justify="right")
        table.add_column("PnL", justify="right")

        if not positions:
            table.add_row("[dim]Sin posiciones abiertas[/]", "", "", "")
            return table

        for key, pos in positions.items():
            strat = key.split("_", 1)[1] if "_" in key else key
            side_str = "[green]LONG[/]" if pos.side.value == "BUY" else "[red]SHORT[/]"
            upnl = pos.update_pnl(price)
            upnl_color = "green" if upnl >= 0 else "red"
            table.add_row(
                strat[:15],
                side_str,
                f"${pos.entry_price:,.2f}",
                f"[{upnl_color}]${upnl:+,.2f}[/]",
            )
        return table

    def _build_regime_micro(self, regime, micro_snap) -> Table:
        table = Table(expand=True, show_header=False, show_lines=False)
        table.add_column(justify="right", style="bold")
        table.add_column(justify="left")

        regime_str = regime.value if regime else "UNKNOWN"
        regime_colors = {
            "TRENDING_UP": "green", "TRENDING_DOWN": "red",
            "MEAN_REVERTING": "yellow", "HIGH_VOLATILITY": "red bold",
            "LOW_VOLATILITY": "dim", "UNKNOWN": "white",
        }
        rc = regime_colors.get(regime_str, "white")
        table.add_row("Regimen:", f"[{rc}]{regime_str}[/]")

        if micro_snap:
            vpin = micro_snap.vpin.vpin
            vpin_color = "red" if micro_snap.vpin.is_toxic else "green"
            table.add_row("VPIN:", f"[{vpin_color}]{vpin:.4f}[/]")

            hawkes = micro_snap.hawkes.spike_ratio
            h_color = "red" if micro_snap.hawkes.is_spike else "green"
            table.add_row("Hawkes:", f"[{h_color}]{hawkes:.2f}x[/]")

            table.add_row("Spread:", f"{micro_snap.avellaneda_stoikov.spread_bps:.1f} bps")
            table.add_row("Risk Score:", f"{micro_snap.risk_score:.2f}")

        return table

    def _build_trades_table(self) -> Table:
        table = Table(expand=True, show_header=True, show_lines=False)
        table.add_column("Tipo", style="cyan", width=14)
        table.add_column("Estrategia", width=14)
        table.add_column("Precio", justify="right", width=12)
        table.add_column("PnL", justify="right", width=12)

        for t in self._last_trades[-6:]:
            exit_type = t.get("exit_type", t.get("side", "?"))
            strat = t.get("strategy", "?")
            price = t.get("exit_price", t.get("price", 0))
            pnl = t.get("pnl", 0)
            pnl_color = "green" if pnl > 0 else "red"
            table.add_row(
                exit_type[:14],
                strat[:14],
                f"${price:,.2f}",
                f"[{pnl_color}]${pnl:+,.2f}[/]",
            )
        return table
