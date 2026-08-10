from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QFormLayout


class ResourcesTab(QWidget):
    def __init__(self, client):
        super().__init__()
        self.client = client
        self.values = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        title = QLabel("Runtime Resources")
        title.setObjectName("h1")
        layout.addWidget(title)
        self.form = QFormLayout()
        layout.addLayout(self.form)
        layout.addStretch()

    def refresh(self):
        try:
            values = self.client.get_resources()
        except Exception:
            return
        self.values = values
        while self.form.rowCount():
            self.form.removeRow(0)
        labels = {
            "proc_cpu": "Process CPU",
            "ram_used_mb": "Process RAM (MB)",
            "ml_inference_ms": "ML Inference (ms)",
            "db_read_ms": "Database Read (ms)",
            "db_write_ms": "Database Write (ms)",
            "events_total": "Events Processed",
            "events_per_sec": "Events per Second",
        }
        for key, name in labels.items():
            self.form.addRow(name, QLabel(str(values.get(key, "-"))))
