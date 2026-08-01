import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication

from desktop_app.server_launcher import ensure_backend_running
from desktop_app.theme import QSS
from desktop_app.login_window import LoginWindow
from desktop_app.main_window import MainWindow


class InSighterApp:
    def __init__(self):
        self.qapp = QApplication(sys.argv)
        self.qapp.setStyleSheet(QSS)
        self.main_window = None
        self.login_window = None

    def start(self):
        ok = ensure_backend_running()
        if not ok:
            print("ERROR: could not start InSighter backend on port 5050.")
            sys.exit(1)

        self._show_login()
        sys.exit(self.qapp.exec())

    def _show_login(self):
        self.login_window = LoginWindow()
        self.login_window.login_succeeded.connect(self._show_main_window)
        self.login_window.show()

    def _show_main_window(self, client):
        self.login_window.close()
        self.main_window = MainWindow(client, on_logout=self._on_logout)
        self.main_window.show()

    def _on_logout(self):
        self.main_window.close()
        self._show_login()


if __name__ == "__main__":
    InSighterApp().start()
