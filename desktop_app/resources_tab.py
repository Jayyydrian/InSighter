import logging

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
)

from desktop_app.async_utils import run_async

logger = logging.getLogger(__name__)

HOST_METRICS = {
    "proc_cpu": ("Process CPU (normalized)", "%"),
    "proc_cpu_raw": ("Process CPU (raw)", "%"),
    "ram_used_mb": ("Process RAM used", "MB"),
    "ram_total_mb": ("Host RAM total", "MB"),
    "ram_percent": ("Process RAM / host RAM", "%"),
    "threads": ("Process threads", "threads"),
    "disk_percent": ("Host disk used", "%"),
    "uptime_s": ("Backend uptime", "s"),
    "ml_inference_ms": ("ML inference time", "ms"),
    "db_read_ms": ("Database read time", "ms"),
    "db_write_ms": ("Database write time", "ms"),
    "last_tick_ms": ("Last simulation tick", "ms"),
    "events_total": ("Events processed", "events"),
    "events_per_sec": ("Event throughput", "events/s"),
}


def _metric_box(label_text, unit):
    box = QFrame()
    box.setObjectName("card")
    layout = QVBoxLayout(box)
    layout.setContentsMargins(18, 14, 18, 14)
    label = QLabel(label_text)
    label.setObjectName("h2")
    value = QLabel("—")
    value.setStyleSheet(
        "font-size:22px; font-weight:700; font-family:Consolas,monospace; "
        "color:#e2e8f0;"
    )
    unit_label = QLabel(unit)
    unit_label.setObjectName("muted")
    layout.addWidget(label)
    layout.addWidget(value)
    layout.addWidget(unit_label)
    box.value_label = value
    box.unit_label = unit_label
    return box


class ResourcesTab(QWidget):
    """Host metrics and live local-LLM performance."""

    def __init__(self, client):
        super().__init__()
        self.client = client
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("Resources")
        title.setObjectName("h1")
        layout.addWidget(title)
        subtitle = QLabel(
            "Host metrics for the InSighter backend, plus local AI analysis performance."
        )
        subtitle.setObjectName("muted")
        layout.addWidget(subtitle)

        host_heading = QLabel("Host metrics")
        host_heading.setObjectName("h2")
        layout.addWidget(host_heading)

        grid = QGridLayout()
        grid.setSpacing(12)
        self.metric_boxes = {
            key: _metric_box(label, unit)
            for key, (label, unit) in HOST_METRICS.items()
        }
        for index, box in enumerate(self.metric_boxes.values()):
            grid.addWidget(box, index // 2, index % 2)
        layout.addLayout(grid)

        self.llm_heading = QLabel("Local AI analysis")
        self.llm_heading.setObjectName("h2")
        layout.addWidget(self.llm_heading)

        self.llm_model_label = QLabel("Model: —")
        self.llm_host_label = QLabel("Ollama host: —")
        self.llm_reachability_label = QLabel("Ollama reachability: not checked")
        self.llm_warmup_label = QLabel("Warm-up: not started")
        for label in (
            self.llm_model_label,
            self.llm_host_label,
            self.llm_reachability_label,
            self.llm_warmup_label,
        ):
            label.setObjectName("muted")
            layout.addWidget(label)

        summary = QHBoxLayout()
        self.llm_data_label = QLabel("No data yet")
        self.llm_last_latency_label = QLabel("Last latency: —")
        self.llm_average_label = QLabel("Rolling average: —")
        self.llm_p95_label = QLabel("P95 latency: —")
        self.llm_calls_label = QLabel("Calls: 0")
        self.llm_errors_label = QLabel("Errors / timeouts: 0 / 0")
        self.llm_tokens_label = QLabel("Generation speed: —")
        for label in (
            self.llm_data_label,
            self.llm_last_latency_label,
            self.llm_average_label,
            self.llm_p95_label,
            self.llm_calls_label,
            self.llm_errors_label,
            self.llm_tokens_label,
        ):
            label.setObjectName("muted")
            summary.addWidget(label)
        layout.addLayout(summary)

        self.recent_calls_heading = QLabel("Recent explanations")
        self.recent_calls_heading.setObjectName("h2")
        layout.addWidget(self.recent_calls_heading)
        self.recent_calls_table = QTableWidget(0, 4)
        self.recent_calls_table.setHorizontalHeaderLabels(
            ["Time", "Model", "Latency (ms)", "Status"]
        )
        self.recent_calls_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.recent_calls_table.setMaximumHeight(220)
        layout.addWidget(self.recent_calls_table)

        self.benchmark_label = QLabel("Benchmark (historical, not live)")
        self.benchmark_label.setObjectName("h2")
        layout.addWidget(self.benchmark_label)
        self.benchmark_table = QTableWidget(0, 6)
        self.benchmark_table.setHorizontalHeaderLabels(
            [
                "Model",
                "Success",
                "Mean (s)",
                "Median (s)",
                "Prompt tokens (mean)",
                "Eval tokens (mean)",
            ]
        )
        self.benchmark_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.benchmark_table.setMaximumHeight(170)
        layout.addWidget(self.benchmark_table)
        layout.addStretch()

    @staticmethod
    def _display_number(value, decimals=1):
        if value is None:
            return "—"
        if isinstance(value, (int, float)):
            return f"{value:.{decimals}f}"
        return str(value)

    @staticmethod
    def _set_table(table, rows, columns):
        table.setRowCount(len(rows))
        for row_index, values in enumerate(rows):
            for column_index, value in enumerate(values):
                table.setItem(
                    row_index,
                    column_index,
                    QTableWidgetItem("—" if value is None else str(value)),
                )

    def refresh(self):
        def fetch():
            results = {}
            for key, method in (
                ("resources", self.client.get_resources),
                ("llm_stats", getattr(self.client, "get_llm_stats", None)),
            ):
                if method is None:
                    results[key] = None
                    continue
                try:
                    results[key] = method()
                except Exception as exc:
                    results[key] = exc
            return results

        run_async(
            fetch,
            self._apply_refresh,
            parent=self,
            key="refresh",
        )

    def _apply_refresh(self, results):
        resources = results.get("resources")
        if isinstance(resources, Exception):
            logger.warning("Failed to refresh host metrics: %s", resources)
            self.llm_reachability_label.setText(
                f"Host metrics unavailable: {resources}"
            )
        elif resources is not None:
            self._apply_host_metrics(resources)

        llm_stats = results.get("llm_stats")
        if isinstance(llm_stats, Exception):
            logger.warning("Failed to refresh local AI statistics: %s", llm_stats)
            self.llm_reachability_label.setText(
                f"Local AI statistics unavailable: {llm_stats}"
            )
        elif llm_stats is not None:
            self._apply_llm_stats(llm_stats)

    def _apply_host_metrics(self, metrics):
        for key, box in self.metric_boxes.items():
            value = metrics.get(key)
            decimals = 0 if key in {"threads", "uptime_s", "events_total"} else 1
            box.value_label.setText(self._display_number(value, decimals))

    def _apply_llm_stats(self, stats):
        self.llm_model_label.setText(f"Model: {stats.get('model') or '—'}")
        scope = stats.get("host_scope", "unknown").replace("_", " ").title()
        self.llm_host_label.setText(
            f"Ollama host: {stats.get('ollama_host') or '—'} ({scope})"
        )

        checked_at = stats.get("last_check_at")
        if stats.get("ollama_reachable"):
            reachability = "Ollama reachable"
        else:
            reachability = "Ollama not running"
        if checked_at:
            reachability += f" · last check {checked_at}"
        self.llm_reachability_label.setText(reachability)
        self.llm_warmup_label.setText(
            f"Warm-up: {stats.get('warmup_state') or 'unknown'}"
        )

        retained = stats.get("retained_calls", 0)
        self.llm_data_label.setText(
            f"{retained} recent call(s)" if retained else "No data yet"
        )
        self.llm_last_latency_label.setText(
            f"Last latency: {self._display_number(stats.get('last_latency_ms'))} ms"
        )
        self.llm_average_label.setText(
            "Rolling average: "
            f"{self._display_number(stats.get('rolling_average_ms'))} ms"
        )
        self.llm_p95_label.setText(
            f"P95 latency: {self._display_number(stats.get('p95_latency_ms'))} ms"
        )
        self.llm_calls_label.setText(f"Calls: {stats.get('call_count', 0)}")
        self.llm_errors_label.setText(
            f"Errors / timeouts: {stats.get('error_count', 0)} / "
            f"{stats.get('timeout_count', 0)}"
        )
        tokens_per_second = stats.get("tokens_per_second")
        self.llm_tokens_label.setText(
            "Generation speed: "
            f"{self._display_number(tokens_per_second, 2)} tokens/s"
        )

        self._set_table(
            self.recent_calls_table,
            [
                (
                    call.get("timestamp"),
                    call.get("model"),
                    self._display_number(call.get("wall_latency_ms")),
                    call.get("status", "error")
                    + (
                        f" ({call['error_type']})"
                        if call.get("error_type")
                        else ""
                    ),
                )
                for call in stats.get("recent_calls", [])
            ],
            4,
        )
        self._set_table(
            self.benchmark_table,
            [
                (
                    row["model"],
                    f"{row['success_rate_percent']:.1f}%",
                    f"{row['mean_generation_time_seconds']:.2f}",
                    f"{row['median_generation_time_seconds']:.2f}",
                    f"{row['mean_prompt_eval_count']:.1f}",
                    f"{row['mean_eval_count']:.1f}",
                )
                for row in stats.get("benchmark", [])
            ],
            6,
        )
