"""
Visualización en tiempo real de la optimización de parámetros.

Muestra:
  - Barra de progreso con ETA
  - Mejor resultado encontrado hasta ahora
  - Top 5 resultados en tabla actualizada en vivo
  - Parámetros actuales siendo evaluados
  - Distribución de métricas (positivos vs negativos)
"""
from __future__ import annotations

import time
from typing import Any

from rich.console import Console, Group
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


class OptimizerLiveDisplay:
    """Muestra el progreso de la optimización en tiempo real."""

    def __init__(self, symbol: str, total_combos: int, metric: str = "sharpe_ratio"):
        self.symbol = symbol
        self.total_combos = total_combos
        self.metric = metric
        self.console = Console()
        self._start_time = time.time()
        self._last_update = 0

        # Stats
        self._positive_pnl = 0
        self._negative_pnl = 0
        self._best_sharpe = -999
        self._best_pnl = -999999
        self._best_params = {}

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
            f"Optimizing {symbol}", total=total_combos
        )

        self._live = Live(
            self._build_display(0, {}, None, None),
            console=self.console,
            refresh_per_second=4,
            transient=False,
        )

    def start(self) -> None:
        self._start_time = time.time()
        self._live.start()

    def stop(self) -> None:
        self._progress.update(self._task_id, completed=self.total_combos)
        self._live.stop()

    def update(
        self,
        combo_idx: int,
        total: int,
        params: dict,
        result: Any,
        gs_result: Any,
        metric: str,
    ) -> None:
        now = time.time()
        if now - self._last_update < 0.25:
            return
        self._last_update = now

        # Update stats
        if result.net_pnl > 0:
            self._positive_pnl += 1
        else:
            self._negative_pnl += 1

        if result.sharpe_ratio > self._best_sharpe:
            self._best_sharpe = result.sharpe_ratio
            self._best_params = params.copy()
        if result.net_pnl > self._best_pnl:
            self._best_pnl = result.net_pnl

        self._progress.update(self._task_id, completed=combo_idx + 1)
        display = self._build_display(combo_idx + 1, params, result, gs_result)
        self._live.update(display)

    def _build_display(self, completed, current_params, current_result, gs_result):
        panels = []

        # 1. Progress
        panels.append(self._progress)

        # 2. Stats + best so far
        stats = self._build_stats(completed, current_params, current_result)
        panels.append(Panel(stats, title="[bold white]Estado", border_style="blue"))

        # 3. Top 5 results
        if gs_result and gs_result.results:
            top_table = self._build_top_table(gs_result)
            panels.append(Panel(top_table, title="[bold white]Top 5 Resultados", border_style="green"))

        # 4. Distribution bar
        if completed > 0:
            dist = self._build_distribution(completed)
            panels.append(Panel(dist, title="[bold white]Distribucion PnL", border_style="cyan"))

        return Group(*panels)

    def _build_stats(self, completed, current_params, current_result):
        table = Table.grid(expand=True, padding=(0, 2))
        table.add_column(justify="right", style="bold")
        table.add_column(justify="left")
        table.add_column(justify="right", style="bold")
        table.add_column(justify="left")

        elapsed = time.time() - self._start_time
        speed = completed / elapsed if elapsed > 0 else 0
        remaining = (self.total_combos - completed) / speed if speed > 0 else 0

        best_color = "green" if self._best_sharpe > 0 else "red"
        pnl_color = "green" if self._best_pnl > 0 else "red"

        table.add_row(
            "Velocidad:", f"{speed:.1f} evals/s",
            "Mejor Sharpe:", f"[{best_color}]{self._best_sharpe:.2f}[/]",
        )
        table.add_row(
            "Evaluadas:", f"{completed}/{self.total_combos}",
            "Mejor PnL:", f"[{pnl_color}]${self._best_pnl:+,.2f}[/]",
        )

        if current_result:
            cr_color = "green" if current_result.net_pnl > 0 else "red"
            table.add_row(
                "Actual PnL:", f"[{cr_color}]${current_result.net_pnl:+,.2f}[/]",
                "Actual Sharpe:", f"{current_result.sharpe_ratio:.2f}",
            )

        if self._best_params:
            short_params = ", ".join(
                f"{k.replace('mr_','').replace('tf_','')}={v}"
                for k, v in self._best_params.items()
            )
            table.add_row(
                "Mejor params:", f"[yellow]{short_params}[/]",
                "", "",
            )

        return table

    def _build_top_table(self, gs_result):
        # Sort current results by metric
        sorted_results = sorted(
            gs_result.results,
            key=lambda r: getattr(r, self.metric, 0),
            reverse=True,
        )[:5]

        table = Table(expand=True, show_header=True, show_lines=False)
        table.add_column("#", width=3, justify="right")
        table.add_column("Sharpe", width=8, justify="right")
        table.add_column("PnL", width=12, justify="right")
        table.add_column("WR", width=7, justify="right")
        table.add_column("PF", width=6, justify="right")
        table.add_column("MaxDD", width=8, justify="right")
        table.add_column("Trades", width=7, justify="right")
        table.add_column("Params", style="dim")

        for i, r in enumerate(sorted_results):
            pnl_color = "green" if r.net_pnl > 0 else "red"
            sharpe_color = "green" if r.sharpe_ratio > 0 else "red"
            params_str = ", ".join(
                f"{k.replace('mr_','').replace('tf_','')}={v}"
                for k, v in r.params.items()
            )
            table.add_row(
                str(i + 1),
                f"[{sharpe_color}]{r.sharpe_ratio:.2f}[/]",
                f"[{pnl_color}]${r.net_pnl:+,.0f}[/]",
                f"{r.win_rate:.1%}",
                f"{r.profit_factor:.2f}",
                f"{r.max_drawdown:.2%}",
                str(r.total_trades),
                params_str[:50],
            )

        return table

    def _build_distribution(self, completed):
        total = self._positive_pnl + self._negative_pnl
        if total == 0:
            return Text("Sin datos")

        pos_pct = self._positive_pnl / total
        neg_pct = self._negative_pnl / total

        bar_width = 60
        pos_bars = int(pos_pct * bar_width)
        neg_bars = bar_width - pos_bars

        text = Text()
        text.append(f"  Rentables: {self._positive_pnl} ({pos_pct:.0%}) ", style="green")
        text.append("\u2588" * pos_bars, style="green")
        text.append("\u2588" * neg_bars, style="red")
        text.append(f" No rentables: {self._negative_pnl} ({neg_pct:.0%})", style="red")

        return text
