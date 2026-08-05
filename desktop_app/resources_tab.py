from PyQt6.QtWidgets import QWidget, QVBoxLayout, QGridLayout, QLabel, QFrame


def _metric_box(label_text, sub_text=""):
    box = QFrame()
    box.setObjectName("card")
    layout = QVBoxLayout(box)
    layout.setContentsMargins(18, 14, 18, 14)
    label = QLabel(label_text)
    label.setObjectName("h2")
    value = QLabel("—")
    value.setStyleSheet("font-size:22px; font-weight:700; font-family:Consolas,monospace; color:#e2e8f0;")
    sub = QLabel(sub_text)
    sub.setObjectName("muted")
    layout.addWidget(label)
    layout.addWidget(value)
    layout.addWidget(sub)
    box.value_label = value
    box.sub_label = sub
    return box


class ResourcesTab(QWidget):
    """Live process and inference performance metrics."""

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
        subtitle = QLabel("Live process and inference performance metrics.")
        subtitle.setObjectName("muted")
        layout.addWidget(subtitle)

        grid = QGridLayout()
        grid.setSpacing(12)

        self.cpu_box = _metric_box("Process CPU %", "InSighter process only")
        self.ram_box = _metric_box("Process RAM", "of total memory")
        self.ml_box = _metric_box("ML Inference Time", "Isolation Forest per tick")
        self.dbr_box = _metric_box("DB Read Time", "SQLite fetch per tick")
        self.dbw_box = _metric_box("DB Write Time", "log injection latency")
        self.tick_box = _metric_box("Total Tick Time", "end-to-end per event")
        self.evts_box = _metric_box("Events Processed", "since last reseed")
        self.eps_box = _metric_box("Throughput", "events / second")
        self.threads_box = _metric_box("Threads", "Flask worker threads")
        self.uptime_box = _metric_box("Process Uptime", "since app started")

        boxes = [self.cpu_box, self.ram_box, self.ml_box, self.dbr_box,
                 self.dbw_box, self.tick_box, self.evts_box, self.eps_box,
                 self.threads_box, self.uptime_box]
        for i, box in enumerate(boxes):
            grid.addWidget(box, i // 2, i % 2)

        layout.addLayout(grid)
        layout.addStretch()

    def refresh(self):
        try:
            d = self.client.get_resources()
        except Exception:
            return

        self.cpu_box.value_label.setText(f'{d["proc_cpu"]}%')
        self.cpu_box.sub_label.setText(f'raw: {d["proc_cpu_raw"]}% across all cores')

        self.ram_box.value_label.setText(f'{d["ram_used_mb"]} MB')
        self.ram_box.sub_label.setText(f'{d["ram_percent"]}% of total')

        fmt = lambda ms: f"{ms:.1f} ms" if ms > 0 else "—"
        self.ml_box.value_label.setText(fmt(d["ml_inference_ms"]))
        self.dbr_box.value_label.setText(fmt(d["db_read_ms"]))
        self.dbw_box.value_label.setText(fmt(d["db_write_ms"]))
        self.tick_box.value_label.setText(fmt(d["last_tick_ms"]))

        self.evts_box.value_label.setText(str(d["events_total"]) if d["events_total"] > 0 else "—")
        self.eps_box.value_label.setText(f'{d["events_per_sec"]}/s' if d["events_per_sec"] > 0 else "—")
        self.threads_box.value_label.setText(str(d["threads"]))

        h, m = divmod(int(d["uptime_s"]) // 60, 60)
        self.uptime_box.value_label.setText(f"{h}h {m}m")
