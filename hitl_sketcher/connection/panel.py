"""Connection settings dock widget."""

from __future__ import annotations

from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtWidgets import (
    QDockWidget,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import PLUGIN_NAME
from .client import BackendClient


class ConnectionPanel(QDockWidget):
    """Dock widget for backend connection configuration."""

    connected = pyqtSignal()  # emitted after successful health check

    def __init__(self, iface, parent=None):
        super().__init__(f"{PLUGIN_NAME} Connection", parent)
        self.iface = iface
        self.client = BackendClient()

        container = QWidget()
        layout = QVBoxLayout(container)

        form = QFormLayout()
        self.url_input = QLineEdit("http://localhost:8000")
        form.addRow("Backend URL:", self.url_input)
        self.api_key_input = QLineEdit()
        self.api_key_input.setPlaceholderText("Leave blank for local dev")
        self.api_key_input.setEchoMode(QLineEdit.Password)
        form.addRow("API Key:", self.api_key_input)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._on_connect)
        btn_row.addWidget(self.connect_btn)

        self.release_btn = QPushButton("Release Session")
        self.release_btn.setToolTip(
            "Relinquish your exclusive lock so another user can connect"
        )
        self.release_btn.setEnabled(False)
        self.release_btn.clicked.connect(self._on_release)
        btn_row.addWidget(self.release_btn)
        layout.addLayout(btn_row)

        self.status_label = QLabel("Not connected")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.session_label = QLabel("")
        self.session_label.setWordWrap(True)
        layout.addWidget(self.session_label)

        self.gpu_label = QLabel("")
        layout.addWidget(self.gpu_label)

        self.setWidget(container)
        self.setMaximumHeight(240)

    def _on_connect(self) -> None:
        """Connect to the backend and acquire the exclusive session lock."""
        url = self.url_input.text().strip()
        api_key = self.api_key_input.text().strip()
        self.client.set_url(url)
        self.client.set_api_key(api_key or None)

        # 1 — Health / identity check
        try:
            result = self.client.connect()
        except PermissionError:
            self.status_label.setText("Invalid API key")
            self.status_label.setStyleSheet("color: red")
            self.gpu_label.setText("")
            self._refresh_session_ui()
            return
        except Exception as e:
            self.status_label.setText(f"Failed: {e}")
            self.status_label.setStyleSheet("color: red")
            self.gpu_label.setText("")
            self._refresh_session_ui()
            return

        gpu = result.get("gpu_active", "none")
        vram = result.get("gpu_vram_total_mb", 0)
        project = result.get("project", "")
        self.status_label.setText(f"Connected — project: {project}")
        self.status_label.setStyleSheet("color: green")
        self.gpu_label.setText(f"GPU: {gpu} | VRAM: {vram:.0f} MB")
        self.connected.emit()

        # 2 — Acquire exclusive session (non-blocking — shows who holds it on failure)
        try:
            self.client.acquire_session()
            self._refresh_session_ui()
        except PermissionError as e:
            self._refresh_session_ui(lock_msg=str(e))
        except Exception:
            self._refresh_session_ui()

    def _on_release(self) -> None:
        """Release the exclusive session lock so another user can connect."""
        try:
            self.client.release_session()
        except Exception:
            pass
        self._refresh_session_ui()

    def _refresh_session_ui(self, lock_msg: str = "") -> None:
        """Update session label and Release button to match current lock state."""
        if self.client.has_session:
            self.session_label.setText("Session: active — you hold the lock")
            self.session_label.setStyleSheet("color: green")
            self.release_btn.setEnabled(True)
        elif lock_msg:
            self.session_label.setText(f"Session: {lock_msg}")
            self.session_label.setStyleSheet("color: orange")
            self.release_btn.setEnabled(False)
        else:
            self.session_label.setText("")
            self.session_label.setStyleSheet("")
            self.release_btn.setEnabled(False)
