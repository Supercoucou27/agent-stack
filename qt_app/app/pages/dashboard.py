from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from collections import deque
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from llama_data.stores import ConfigStore

from .. import theme
from ..services.runtime import LlamaServerController, LogLine, RuntimeStatus, ServerState
from ..services.runtime_api import ApiStatus, RuntimeMetrics
from ..widgets.buttons import SecondaryButton
from ..widgets.cards import Card, CardTitle, Chip, FieldTile
from .base import PageBase, PagePolicy


class _RollingChart(QWidget):
    """Small dependency-free rolling line/area chart."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        color: str = theme.ACCENT,
        fill_color: str | None = None,
        suffix: str = "",
        dynamic_slot_color: bool = False,
    ) -> None:
        super().__init__(parent)
        self._values: deque[float] = deque(maxlen=120)
        self._color = color
        self._fill_color = fill_color or self._with_alpha(color, 48)
        self._suffix = suffix
        self._dynamic_slot_color = dynamic_slot_color
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAutoFillBackground(False)

    def add_value(self, value: float) -> None:
        self._values.append(max(0.0, float(value)))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.fillRect(rect, QColor(theme.BG_INSET))

        plot = rect.adjusted(42, 18, -12, -30)
        painter.setPen(QPen(QColor(theme.BORDER_SOFT), 1))
        for i in range(5):
            y = plot.top() + (plot.height() * i / 4)
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        for i in range(4):
            x = plot.left() + (plot.width() * i / 3)
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))

        values = list(self._values)
        current = values[-1] if values else 0.0
        ymax = max(values) if values else 1.0
        ymax = max(1.0, ymax * 1.1)
        color = self._line_color(current)

        if values:
            points: list[QPointF] = []
            denom = max(1, len(values) - 1)
            for idx, value in enumerate(values):
                x = plot.left() + (plot.width() * idx / denom)
                y = plot.bottom() - (plot.height() * min(value, ymax) / ymax)
                points.append(QPointF(x, y))

            area = QPainterPath()
            area.moveTo(points[0].x(), plot.bottom())
            for point in points:
                area.lineTo(point)
            area.lineTo(points[-1].x(), plot.bottom())
            area.closeSubpath()
            painter.fillPath(area, self._fill_brush(color))

            line = QPainterPath(points[0])
            for point in points[1:]:
                line.lineTo(point)
            painter.setPen(QPen(QColor(color), 2))
            painter.drawPath(line)

        font = QFont(theme.FONT_MONO.family, theme.FONT_MONO.base_px)
        painter.setFont(font)
        painter.setPen(QColor(theme.FG_MUTED))
        painter.drawText(QRectF(4, plot.top() - 2, 36, 16), Qt.AlignmentFlag.AlignRight, self._format_value(ymax))
        painter.drawText(QRectF(4, plot.bottom() - 12, 36, 16), Qt.AlignmentFlag.AlignRight, "0")
        painter.drawText(QRectF(plot.left(), plot.bottom() + 8, 80, 16), Qt.AlignmentFlag.AlignLeft, "-2m")
        painter.drawText(QRectF(plot.right() - 80, plot.bottom() + 8, 80, 16), Qt.AlignmentFlag.AlignRight, "now")

        value_font = QFont(theme.FONT_MONO.family, theme.FONT_MONO.base_px + 3)
        value_font.setBold(True)
        painter.setFont(value_font)
        painter.setPen(QColor(color))
        painter.drawText(rect.adjusted(0, 8, -14, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight, self._format_value(current))

    def _line_color(self, current: float) -> str:
        if self._dynamic_slot_color:
            return theme.DANGER if current >= 80.0 else theme.SUCCESS
        return self._color

    def _fill_brush(self, color: str) -> QColor:
        if self._dynamic_slot_color:
            return QColor(self._with_alpha(color, 48))
        return QColor(self._fill_color)

    def _format_value(self, value: float) -> str:
        if self._suffix == "%":
            return f"{value:.0f}%"
        if value >= 100:
            return f"{value:.0f}{self._suffix}"
        if value >= 10:
            return f"{value:.1f}{self._suffix}"
        return f"{value:.2f}{self._suffix}"

    @staticmethod
    def _with_alpha(color: str, alpha: int) -> str:
        qcolor = QColor(color)
        qcolor.setAlpha(alpha)
        return qcolor.name(QColor.NameFormat.HexArgb)


class DashboardPage(PageBase):
    policy = PagePolicy.FULL_WIDTH

    def __init__(self, parent=None):
        self._controller: Optional[LlamaServerController] = None
        self._config_store = ConfigStore.default()
        self._metrics: deque[dict] = deque(maxlen=120)
        self._last_counter_sample: tuple[float, float, float] | None = None
        self._smoothed_tps: float = 0.0
        super().__init__(parent)

    def set_controller(self, controller: LlamaServerController) -> None:
        self._controller = controller
        self._refresh()

    def build(self) -> None:
        self.setProperty("subtitle", "Real-time server metrics, throughput charts, and streaming logs.")
        self._build_status_card()
        self._build_metrics_row()
        self._build_log_viewer()

        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    def _build_status_card(self) -> None:
        card = Card(self._body)
        card.setObjectName("PageCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.addWidget(CardTitle("Server status", card))
        header.addStretch(1)
        self._state_chip = Chip("Stopped", "muted", card)
        header.addWidget(self._state_chip)
        layout.addLayout(header)

        fields = QHBoxLayout()
        fields.setSpacing(10)
        self._model_tile = FieldTile("Model", "—", card)
        self._host_tile = FieldTile("Host", "—", card)
        self._pid_tile = FieldTile("PID", "—", card)
        self._uptime_tile = FieldTile("Uptime", "—", card)
        self._slots_tile = FieldTile("Slots", "—", card)
        self._prompt_tile = FieldTile("Prompt tokens", "—", card)
        self._generated_tile = FieldTile("Generated tokens", "—", card)
        self._total_tokens_tile = FieldTile("Total tokens", "—", card)
        self._params_tile = FieldTile("Parameters", "—", card)
        self._ctx_tile = FieldTile("Context", "—", card)
        self._size_tile = FieldTile("Model size", "—", card)
        for tile in (
            self._model_tile,
            self._host_tile,
            self._pid_tile,
            self._uptime_tile,
            self._slots_tile,
            self._prompt_tile,
            self._generated_tile,
            self._total_tokens_tile,
            self._params_tile,
            self._ctx_tile,
            self._size_tile,
        ):
            fields.addWidget(tile)
        layout.addLayout(fields)
        self._layout.addWidget(card)

    def _build_metrics_row(self) -> None:
        row = QHBoxLayout()
        row.setSpacing(14)
        self._throughput_chart = _RollingChart(self._body, color=theme.ACCENT, fill_color=theme.ACCENT_DIM, suffix=" tok/s")
        self._latency_chart = _RollingChart(self._body, color=theme.WARNING, suffix=" ms")
        self._slot_chart = _RollingChart(self._body, color=theme.SUCCESS, suffix="%", dynamic_slot_color=True)
        row.addWidget(self._chart_card("Throughput", self._throughput_chart), 1)
        row.addWidget(self._chart_card("Health latency", self._latency_chart), 1)
        row.addWidget(self._chart_card("Slot utilization", self._slot_chart), 1)
        self._layout.addLayout(row)

    def _chart_card(self, title: str, chart: _RollingChart) -> Card:
        card = Card(self._body)
        card.setObjectName("PageCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(CardTitle(title, card))
        layout.addWidget(chart)
        return card

    def _build_log_viewer(self) -> None:
        logs = Card(self._body)
        logs.setObjectName("PageCard")
        logs_layout = QVBoxLayout(logs)
        logs_layout.setContentsMargins(16, 14, 16, 14)
        logs_layout.setSpacing(8)
        logs_layout.addWidget(CardTitle("Streaming logs", logs))

        filter_row = QHBoxLayout()
        self.log_search = QLineEdit(logs)
        self.log_search.setPlaceholderText("Search logs…")
        self.log_search.textChanged.connect(self._render_logs)
        self.log_source = QComboBox(logs)
        self.log_source.addItems(["all", "stdout", "stderr"])
        self.log_source.currentIndexChanged.connect(self._render_logs)
        copy_btn = SecondaryButton("Copy", logs)
        copy_btn.clicked.connect(self._copy_logs)
        clear_btn = SecondaryButton("Clear", logs)
        clear_btn.clicked.connect(self._clear_logs)
        filter_row.addWidget(self.log_search, 1)
        filter_row.addWidget(self.log_source)
        filter_row.addWidget(copy_btn)
        filter_row.addWidget(clear_btn)
        logs_layout.addLayout(filter_row)

        self.logs = QPlainTextEdit(logs)
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(10000)
        self.logs.setMinimumHeight(320)
        self.logs.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.logs.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {theme.BG_INSET}; border: 1px solid {theme.BORDER}; "
            f"border-radius: 6px; color: {theme.FG_PRIMARY}; {theme.font_css(theme.FONT_MONO)} }}"
        )
        logs_layout.addWidget(self.logs)

        footer = QHBoxLayout()
        self.autoscroll = QCheckBox("Auto-scroll", logs)
        self.autoscroll.setChecked(True)
        self.line_count = QLabel("Showing 0 of 0 lines", logs)
        self.line_count.setObjectName("Muted")
        footer.addWidget(self.autoscroll)
        footer.addStretch(1)
        footer.addWidget(self.line_count)
        logs_layout.addLayout(footer)
        self._layout.addWidget(logs)

    def _poll(self) -> None:
        config = self._config_store.load()
        status, runtime_metrics = self._monitor_status_and_metrics()
        elapsed_ms = 0.0
        api = status.api_status

        now = time.time()
        log_lines = self._controller.log_buffer.lines() if self._controller else []
        prompt_tokens = self._metric_counter(
            runtime_metrics,
            "llamacpp_prompt_tokens_total",
            "llamacpp_tokens_evaluated_total",
            "llamacpp_prompt_tokens",
            "llamacpp_tokens_evaluated",
            "prompt_tokens_total",
            "prompt_tokens",
            "tokens_evaluated_total",
            "tokens_evaluated",
        )
        generated_tokens = self._metric_counter(
            runtime_metrics,
            "llamacpp_generation_tokens_total",
            "llamacpp_tokens_predicted_total",
            "llamacpp_generation_tokens",
            "llamacpp_tokens_predicted",
            "generation_tokens_total",
            "generation_tokens",
            "tokens_predicted_total",
            "tokens_predicted",
        )
        prompt_tps, generation_tps = self._token_rates(now, prompt_tokens, generated_tokens)
        log_prompt_tps, log_generation_tps = self._log_token_rates(log_lines)
        if prompt_tps <= 0.0:
            prompt_tps = log_prompt_tps
        if generation_tps <= 0.0:
            generation_tps = log_generation_tps
        active_slots, total_slots = self._slot_counts(api, runtime_metrics, log_lines)

        self._update_status(status, active_slots, total_slots, prompt_tokens, generated_tokens)

        metric = {
            "timestamp": now,
            "reachable": bool(api and api.reachable),
            "health": api.health if api else None,
            "latency_ms": elapsed_ms,
            "tokens_per_second": self._smooth_tps(prompt_tps + generation_tps, now),
            "prompt_tokens_per_second": prompt_tps,
            "generation_tokens_per_second": generation_tps,
            "prompt_tokens": prompt_tokens,
            "generated_tokens": generated_tokens,
            "total_tokens": prompt_tokens + generated_tokens,
            "slot_utilization": (active_slots / total_slots * 100.0) if total_slots else 0.0,
        }
        self._metrics.append(metric)
        self._throughput_chart.add_value(metric["tokens_per_second"])
        self._latency_chart.add_value(metric["latency_ms"])
        self._slot_chart.add_value(metric["slot_utilization"])

        self._render_logs()


    def _refresh(self) -> None:
        self._poll()

    def _update_status(
        self,
        status,
        active_slots: int = 0,
        total_slots: int | None = None,
        prompt_tokens: float = 0.0,
        generated_tokens: float = 0.0,
    ) -> None:
        self._set_state_chip(status.state)
        model_name = self._current_model_name(None, None, status.model_path)
        self._model_tile.set_value(model_name)
        self._host_tile.set_value(f"{status.host}:{status.port}")
        self._pid_tile.set_value(str(status.pid) if status.pid is not None else "—")
        self._uptime_tile.set_value(self._format_uptime())
        self._slots_tile.set_value(f"{active_slots} / {total_slots}" if total_slots else "—")
        self._prompt_tile.set_value(f"{int(prompt_tokens):,}" if prompt_tokens else "—")
        self._generated_tile.set_value(f"{int(generated_tokens):,}" if generated_tokens else "—")
        total_tokens = prompt_tokens + generated_tokens
        self._total_tokens_tile.set_value(f"{int(total_tokens):,}" if total_tokens else "—")
        self._params_tile.set_value("—")
        self._ctx_tile.set_value("—")
        self._size_tile.set_value("—")
    def _set_state_chip(self, state: ServerState) -> None:
        text = state.value.title()
        self._state_chip.setText(text)
        self._state_chip.setStyleSheet("")
        if state == ServerState.HEALTHY:
            self._state_chip.set_style("success")
        elif state in {ServerState.ERROR, ServerState.UNHEALTHY, ServerState.EXITED}:
            self._state_chip.set_style("muted")
            self._state_chip.setStyleSheet(
                f"background-color: {theme.DANGER}; color: {theme.FG_PRIMARY}; border-radius: 9px; padding: 2px 8px;"
            )
        elif state in {ServerState.STARTING, ServerState.RUNNING, ServerState.STOPPING}:
            self._state_chip.set_style("warning")
        else:
            self._state_chip.set_style("muted")

    def _render_logs(self) -> None:
        if not self._controller:
            self.logs.setPlainText("Remote llama-server logs are not exposed by llama-server's HTTP API. Metrics and health are still monitored from the configured endpoint.")
            self.line_count.setText("Showing 0 of 0 lines")
            return
        query = self.log_search.text().strip().lower()
        source = self.log_source.currentText()
        lines: list[LogLine] = self._controller.log_buffer.lines()
        rendered: list[str] = []
        for line in lines:
            if source != "all" and line.source != source:
                continue
            if query and query not in line.text.lower():
                continue
            rendered.append(f"{line.timestamp} [{line.source}] {line.text}")
        visible = rendered[-10000:]
        self.logs.setPlainText("\n".join(visible))
        if self.autoscroll.isChecked():
            cursor = self.logs.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.logs.setTextCursor(cursor)
        self.line_count.setText(f"Showing {len(visible)} of {len(lines)} lines")

    def _copy_logs(self) -> None:
        text = self.logs.toPlainText()
        if text:
            QApplication.clipboard().setText(text)

    def _clear_logs(self) -> None:
        if self._controller:
            self._controller.log_buffer.clear()
        self.logs.clear()
        self.line_count.setText("Showing 0 of 0 lines")

    def _format_uptime(self) -> str:
        if not self._metrics:
            return "—"
        seconds = max(0, int(time.time() - float(self._metrics[0]["timestamp"])))
        minutes, sec = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}h {minutes:02d}m {sec:02d}s"
        if minutes:
            return f"{minutes}m {sec:02d}s"
        return f"{sec}s"

    def _slot_counts(
        self,
        api: ApiStatus | None,
        runtime_metrics: RuntimeMetrics | None,
        log_lines: list[LogLine] | None = None,
    ) -> tuple[int, int | None]:
        total = self._metric_counter(
            runtime_metrics,
            "llamacpp_slots_total",
            "slots_total",
            "llama_slots_total",
        )

        active = self._metric_counter(
            runtime_metrics,
            "llamacpp_slots_processing",
            "llamacpp_slots_active",
            "slots_processing",
            "slots_active",
            "llama_slots_processing",
            "llamacpp_slot_state",
            "slot_state",
        )
        if not active and api and api.slots_processing is not None:
            active = float(api.slots_processing)
        if not active and runtime_metrics and runtime_metrics.values:
            active = sum(
                value
                for name, value in runtime_metrics.values.items()
                if "slot" in name and ("processing" in name or "active" in name)
            )
        if not active and total and runtime_metrics:
            idle = self._metric_counter(runtime_metrics, "llamacpp_slots_idle", "slots_idle", "llama_slots_idle")
            if idle:
                active = max(0.0, total - idle)
        if not active and log_lines:
            active = float(self._active_slots_from_logs(log_lines))

        return int(active), int(total) if total else None

    def _monitor_status_and_metrics(self) -> tuple[RuntimeStatus, RuntimeMetrics | None]:
        config = self._config_store.load()

        # Remote monitor mode — connect to an arbitrary server.
        if config.remote_monitor_enabled:
            host = config.remote_monitor_host
            port = config.remote_monitor_port
            from ..services.runtime_api import LlamaServerApiClient
            client = LlamaServerApiClient(host, port)
            metrics = client.fetch_metrics()
            state = ServerState.HEALTHY if metrics and metrics.reachable else ServerState.UNHEALTHY
            return RuntimeStatus(
                state=state,
                host=host,
                port=port,
                model_path=None,
                api_status=ApiStatus(
                    reachable=bool(metrics and metrics.reachable),
                    health="ok" if metrics and metrics.reachable else "unreachable",
                ),
            ), metrics

        # Local mode (single-model or router).
        if not self._controller:
            return RuntimeStatus(
                state=ServerState.STOPPED,
                host=config.host,
                port=config.port,
                model_path=None,
                api_status=None,
            ), None

        status = self._controller.status
        client = self._controller._api_client
        if not client:
            return status, None

        # Router mode: /metrics requires ?model=<name>.  Discover the
        # first loaded model and target it explicitly.  With
        # --no-models-autoload these calls are safe — no loads/evictions.
        if config.router_mode:
            loaded = client.list_loaded_models()
            loaded_id = None
            for m in loaded:
                s = m.get("status") or m.get("state") or ""
                # Router returns status as {"value": "loaded"} or bare string.
                if isinstance(s, dict):
                    s = s.get("value", "")
                if s in ("loaded", "loading"):
                    loaded_id = m.get("id")
                    break
            metrics = client.fetch_metrics(model=loaded_id) if loaded_id else None
            if status.state in {ServerState.RUNNING, ServerState.HEALTHY, ServerState.UNHEALTHY}:
                if metrics and metrics.reachable:
                    status.api_status = ApiStatus(reachable=True, health="ok")
                else:
                    status.api_status = ApiStatus(reachable=True, health="no model loaded")
            return status, metrics

        # Single-model mode: /metrics works without a model parameter.
        metrics = client.fetch_metrics()
        if status.state in {ServerState.RUNNING, ServerState.HEALTHY, ServerState.UNHEALTHY}:
            status.api_status = ApiStatus(
                reachable=bool(metrics and metrics.reachable),
                health="ok" if metrics and metrics.reachable else "unreachable",
            )
        return status, metrics

    def _token_rates(self, timestamp: float, prompt_tokens: float, generated_tokens: float) -> tuple[float, float]:
        previous = self._last_counter_sample
        self._last_counter_sample = (timestamp, prompt_tokens, generated_tokens)
        if previous is None:
            return 0.0, 0.0
        previous_time, previous_prompt, previous_generated = previous
        elapsed = max(1e-6, timestamp - previous_time)
        prompt_delta = max(0.0, prompt_tokens - previous_prompt)
        generated_delta = max(0.0, generated_tokens - previous_generated)
        return prompt_delta / elapsed, generated_delta / elapsed

    @staticmethod
    def _log_token_rates(log_lines: list[LogLine]) -> tuple[float, float]:
        prompt_rate = 0.0
        generation_rate = 0.0
        for line in reversed(log_lines[-200:]):
            text = line.text
            if generation_rate <= 0.0:
                match = re.search(r"\btg\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*t/s\b", text)
                if match:
                    generation_rate = float(match.group(1))
            if prompt_rate <= 0.0:
                match = re.search(r"\bpp\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*t/s\b", text)
                if match:
                    prompt_rate = float(match.group(1))
            if prompt_rate > 0.0 and generation_rate > 0.0:
                break
        return prompt_rate, generation_rate

    @staticmethod
    def _active_slots_from_logs(log_lines: list[LogLine]) -> int:
        active: set[str] = set()
        now = datetime.now(timezone.utc)
        for line in log_lines[-500:]:
            text = line.text
            match = re.search(r"\bslot (?:launch_slot|process|update_slots)\s*: id\s+(\d+)\b", text)
            if match:
                active.add(match.group(1))
                continue
            match = re.search(r"\bslot (?:release|reset|prompt_clear|process_end)\s*: id\s+(\d+)\b", text)
            if match:
                active.discard(match.group(1))
                continue
            match = re.search(r"\bslot print_timing\s*: id\s+(\d+)\b", text)
            if match and DashboardPage._log_line_age_seconds(line, now) <= 10.0:
                active.add(match.group(1))
        return len(active)

    @staticmethod
    def _log_line_age_seconds(line: LogLine, now: datetime) -> float:
        try:
            timestamp = datetime.fromisoformat(line.timestamp)
        except ValueError:
            return 999999.0
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return max(0.0, (now - timestamp).total_seconds())

    def _smooth_tps(self, raw_tps: float, now: float) -> float:
        """Exponential moving average that decays over ~6 seconds.

        Instantaneous counter-delta rates spike then hard-drop to zero
        between generation bursts.  Smoothing makes the chart readable.
        """
        alpha = 0.35
        self._smoothed_tps = alpha * raw_tps + (1 - alpha) * self._smoothed_tps
        # Snap to zero if below noise floor (avoids perpetual 0.1 t/s).
        if self._smoothed_tps < 0.5:
            self._smoothed_tps = 0.0
        return self._smoothed_tps

    @staticmethod
    def _metric_counter(runtime_metrics: RuntimeMetrics | None, *names: str) -> float:
        if not runtime_metrics or not runtime_metrics.reachable:
            return 0.0
        for name in names:
            value = runtime_metrics.values.get(name)
            if value is not None:
                return value
        return 0.0

    @staticmethod
    def _current_model_name(_router_models: list[dict] | None, _api_model_path: str | None, status_model_path: str | None) -> str:
        if status_model_path and status_model_path != "none" and Path(status_model_path).suffix:
            return Path(status_model_path).name
        return "—"


__all__ = ["DashboardPage"]
