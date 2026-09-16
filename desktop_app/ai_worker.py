from PyQt6.QtCore import QThread, pyqtSignal


class AiAnalysisWorker(QThread):
    result_ready = pyqtSignal(dict)

    def __init__(self, client, alert_id):
        super().__init__()
        self.client = client
        self.alert_id = alert_id

    def run(self):
        try:
            result = self.client.explain_alert(self.alert_id)
        except Exception as exc:
            result = {
                "available": False,
                "reason": str(exc) or "AI analysis unavailable",
                "explanation": None,
                "recommendation": None,
            }
        self.result_ready.emit(result)
