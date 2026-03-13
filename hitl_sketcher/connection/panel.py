"""Connection settings dock widget."""

from __future__ import annotations

from qgis.PyQt.QtCore import QTimer, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QDockWidget,
    QFormLayout,
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

        # UI
        container = QWidget()
        layout = QVBoxLayout(container)

        form = QFormLayout()
        self.url_input = QLineEdit("http://localhost:8000")
        form.addRow("Backend URL:", self.url_input)
        layout.addLayout(form)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._on_connect)
        layout.addWidget(self.connect_btn)

        self.status_label = QLabel("Not connected")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.gpu_label = QLabel("")
        layout.addWidget(self.gpu_label)

        layout.addStretch()
        self.setWidget(container)

    def _on_connect(self) -> None:
        """Test backend connection."""
        url = self.url_input.text().strip()
        self.client.set_url(url)
        try:
            result = self.client.health_check()
            status = result.get("status", "unknown")
            gpu = result.get("gpu_active", "none")
            vram = result.get("gpu_vram_mb", 0)
            self.status_label.setText(f"Connected ({status})")
            self.status_label.setStyleSheet("color: green")
            self.gpu_label.setText(f"GPU: {gpu} | VRAM: {vram:.0f} MB")
            self.connected.emit()
        except Exception as e:
            self.status_label.setText(f"Failed: {e}")
            self.status_label.setStyleSheet("color: red")
            self.gpu_label.setText("")
