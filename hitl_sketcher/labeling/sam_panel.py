"""SAM3 interactive labeling control panel.

Provides controls for SAM3 interactive segmentation:
- Mode selector: point click vs box draw
- Accept/reject mask buttons
- Class and region assignment
- Session status display
"""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path
from typing import Optional

from .. import PLUGIN_NAME
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class SAMPanel(QDockWidget):
    """Dock widget for SAM3 interactive labeling controls."""

    # Signals
    mode_changed = pyqtSignal(str)  # "click" or "box"
    mask_accepted = pyqtSignal()
    mask_rejected = pyqtSignal()
    session_started = pyqtSignal(str)  # image path

    def __init__(self, iface, client, class_panel=None):
        super().__init__("SAM3 Labeling", iface.mainWindow())
        self.iface = iface
        self.client = client
        self.class_panel = class_panel
        self._current_mode = "click"
        self._session_active = False

        self._setup_ui()

    def _setup_ui(self):
        widget = QWidget()
        layout = QVBoxLayout()

        # --- Session controls ---
        session_layout = QHBoxLayout()
        self._capture_btn = QPushButton("Capture && Set Image")
        self._capture_btn.setToolTip("Capture visible extent and load into SAM3")
        self._capture_btn.clicked.connect(self._on_capture)
        session_layout.addWidget(self._capture_btn)

        self._reset_btn = QPushButton("Reset Session")
        self._reset_btn.clicked.connect(self._on_reset)
        self._reset_btn.setEnabled(False)
        session_layout.addWidget(self._reset_btn)
        layout.addLayout(session_layout)

        # --- Status ---
        self._status_label = QLabel("No active session")
        layout.addWidget(self._status_label)

        # --- Mode selector ---
        mode_layout = QHBoxLayout()
        mode_layout.addWidget(QLabel("Mode:"))
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["Point Click", "Box Draw"])
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_layout.addWidget(self._mode_combo)
        layout.addLayout(mode_layout)

        # --- Class selector ---
        class_layout = QHBoxLayout()
        class_layout.addWidget(QLabel("Class:"))
        self._class_combo = QComboBox()
        class_layout.addWidget(self._class_combo)
        layout.addLayout(class_layout)

        # --- Region selector ---
        region_layout = QHBoxLayout()
        region_layout.addWidget(QLabel("Region:"))
        self._region_combo = QComboBox()
        region_layout.addWidget(self._region_combo)
        layout.addLayout(region_layout)

        # --- Accept/Reject ---
        action_layout = QHBoxLayout()
        self._accept_btn = QPushButton("Accept Mask (Enter)")
        self._accept_btn.clicked.connect(self._on_accept)
        self._accept_btn.setEnabled(False)
        self._accept_btn.setStyleSheet("background-color: #4CAF50; color: white;")
        action_layout.addWidget(self._accept_btn)

        self._reject_btn = QPushButton("Reject (Esc)")
        self._reject_btn.clicked.connect(self._on_reject)
        self._reject_btn.setEnabled(False)
        self._reject_btn.setStyleSheet("background-color: #f44336; color: white;")
        action_layout.addWidget(self._reject_btn)
        layout.addLayout(action_layout)

        # --- Score display ---
        self._score_label = QLabel("")
        layout.addWidget(self._score_label)

        layout.addStretch()
        widget.setLayout(layout)
        self.setWidget(widget)

    def refresh_classes(self):
        """Refresh class dropdown from backend."""
        self._class_combo.clear()
        try:
            classes = self.client.get_classes()
            for c in classes:
                self._class_combo.addItem(
                    f"{c['class_id']}: {c['name']}", c["class_id"]
                )
        except Exception:
            pass

    def refresh_regions(self):
        """Refresh region dropdown from backend."""
        self._region_combo.clear()
        try:
            crs = self.iface.mapCanvas().mapSettings().destinationCrs().authid()
            regions = self.client.get_regions(crs=crs)
            for r in regions:
                self._region_combo.addItem(
                    f"Region {r['region_id']}", r["region_id"]
                )
        except Exception:
            pass

    def get_active_class_id(self) -> int:
        """Get the currently selected class ID."""
        data = self._class_combo.currentData()
        return data if data is not None else 2

    def get_active_region_id(self) -> int:
        """Get the currently selected region ID."""
        data = self._region_combo.currentData()
        return data if data is not None else 1

    def get_mode(self) -> str:
        """Get the current interaction mode: 'click' or 'box'."""
        return self._current_mode

    def set_mask_available(self, available: bool, score: float = 0.0):
        """Update UI when a mask prediction is available."""
        self._accept_btn.setEnabled(available)
        self._reject_btn.setEnabled(available)
        if available:
            self._score_label.setText(f"Mask score: {score:.3f}")
        else:
            self._score_label.setText("")

    def set_session_active(self, active: bool, info: str = ""):
        """Update session status display."""
        self._session_active = active
        self._reset_btn.setEnabled(active)
        if active:
            self._status_label.setText(f"Session active: {info}")
        else:
            self._status_label.setText("No active session")
            self.set_mask_available(False)

    def _on_capture(self):
        """Capture current extent and upload to SAM3."""
        from ..raster.capture import RasterCapture

        capture = RasterCapture(self.iface)
        path = capture.capture_current_extent()
        if path is None:
            return

        self._status_label.setText("Loading image into SAM3...")
        self._capture_btn.setEnabled(False)

        try:
            result = self.client.sam_set_image(path)
            session_id = result.get("session_id", "?")
            img_size = result.get("image_size", [0, 0])
            self.set_session_active(True, f"{session_id} ({img_size[0]}x{img_size[1]})")
            self.refresh_classes()
            self.refresh_regions()
            self.session_started.emit(path)
        except Exception as e:
            self.iface.messageBar().pushMessage(
                PLUGIN_NAME, f"SAM3 failed: {e}", level=2, duration=5
            )
            self.set_session_active(False)
        finally:
            self._capture_btn.setEnabled(True)

    def _on_reset(self):
        """Reset SAM3 session."""
        try:
            self.client.sam_reset()
        except Exception:
            pass
        self.set_session_active(False)
        self.mask_rejected.emit()

    def _on_mode_changed(self, index):
        self._current_mode = "click" if index == 0 else "box"
        self.mode_changed.emit(self._current_mode)

    def _on_accept(self):
        """Accept the current mask and save as annotation."""
        crs = self.iface.mapCanvas().mapSettings().destinationCrs().authid()

        try:
            result = self.client.sam_accept(
                class_id=self.get_active_class_id(),
                region_id=self.get_active_region_id(),
                crs=crs,
            )
            self.iface.messageBar().pushMessage(
                PLUGIN_NAME, "Annotation saved (SAM3)", level=0, duration=2
            )
            self.set_mask_available(False)
            self.mask_accepted.emit()
        except Exception as e:
            self.iface.messageBar().pushMessage(
                PLUGIN_NAME, f"Save failed: {e}", level=2, duration=5
            )

    def _on_reject(self):
        """Reject the current mask."""
        self.set_mask_available(False)
        self.mask_rejected.emit()
