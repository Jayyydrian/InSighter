from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QLineEdit, QPushButton, QFrame, QHBoxLayout
)
from PyQt6.QtCore import Qt, pyqtSignal

from desktop_app.api_client import ApiClient, ApiError


class LoginWindow(QWidget):
    login_succeeded = pyqtSignal(object)  # emits the ApiClient once authenticated

    def __init__(self):
        super().__init__()
        self.client = ApiClient()
        self.setWindowTitle("InSighter · Sign In")
        self.setFixedSize(420, 480)
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch()

        card = QFrame()
        card.setObjectName("card")
        card.setFixedWidth(340)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(32, 32, 32, 32)
        card_layout.setSpacing(4)

        logo_row = QHBoxLayout()
        dot = QLabel("●")
        dot.setStyleSheet("color:#3b82f6; font-size:10px;")
        title = QLabel("InSighter")
        title.setObjectName("h1")
        logo_row.addWidget(dot)
        logo_row.addWidget(title)
        logo_row.addStretch()
        card_layout.addLayout(logo_row)

        subtitle = QLabel("Insider Threat Monitoring · Sign in to continue")
        subtitle.setObjectName("muted")
        card_layout.addWidget(subtitle)
        card_layout.addSpacing(4)

        card_layout.addWidget(self._field_label("Username"))
        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("admin")
        card_layout.addWidget(self.username_input)

        card_layout.addWidget(self._field_label("Password"))
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("••••••••")
        self.password_input.returnPressed.connect(self._attempt_login)
        card_layout.addWidget(self.password_input)

        card_layout.addSpacing(6)
        self.login_btn = QPushButton("Sign In")
        self.login_btn.setObjectName("primary")
        self.login_btn.clicked.connect(self._attempt_login)
        card_layout.addWidget(self.login_btn)

        self.error_label = QLabel("")
        self.error_label.setObjectName("error")
        self.error_label.setWordWrap(True)
        card_layout.addWidget(self.error_label)

        card_layout.addSpacing(6)
        hint = QLabel(
            "Demo accounts:\nadmin / admin123  (full access)\n"
            "manager / manager123  (summary-only)\n"
            "hr_officer / hr_officer123  (compliance)"
        )
        hint.setObjectName("muted")
        hint.setStyleSheet("border-top:1px solid #1e2d45; padding-top:14px; line-height:1.6;")
        card_layout.addWidget(hint)

        center_row = QHBoxLayout()
        center_row.addStretch()
        center_row.addWidget(card)
        center_row.addStretch()
        outer.addLayout(center_row)
        outer.addStretch()

    def _field_label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#64748b; font-size:11px; margin-top:8px;")
        return lbl

    def _attempt_login(self):
        username = self.username_input.text().strip()
        password = self.password_input.text()
        if not username or not password:
            self.error_label.setText("Please enter both username and password.")
            return

        self.login_btn.setEnabled(False)
        self.login_btn.setText("Signing in…")
        self.error_label.setText("")

        try:
            success = self.client.login(username, password)
        except ApiError as exc:
            success = False
            self.error_label.setText(exc.message)
        except Exception as exc:
            success = False
            self.error_label.setText(f"Could not reach backend: {exc}")

        self.login_btn.setEnabled(True)
        self.login_btn.setText("Sign In")

        if success:
            self.login_succeeded.emit(self.client)
        else:
            if not self.error_label.text():
                self.error_label.setText("Invalid username or password.")
