"""Unified labeling control panel.

Combines:
- Class selector
- Region list with annotation counts + delete
- SAM3 session controls (capture, mode, accept/reject)
- Annotation management (delete selected)
"""

from __future__ import annotations

import logging

from .. import PLUGIN_NAME

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDockWidget,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class LabelingPanel(QDockWidget):
    """Dock widget for labeling controls: classes, regions, SAM3, annotations."""

    # Signals
    mode_changed = pyqtSignal(str)  # "click" or "box"
    mask_accepted = pyqtSignal()
    mask_rejected = pyqtSignal()
    session_started = pyqtSignal(str)  # image path
    layers_changed = pyqtSignal()  # region/annotation added or deleted
    add_region_requested = pyqtSignal()  # user clicked "Add Region"

    def __init__(self, iface, client, class_panel=None):
        super().__init__(f"{PLUGIN_NAME} Labeling", iface.mainWindow())
        self.iface = iface
        self.client = client
        self.class_panel = class_panel
        self._current_mode = "click"
        self._session_active = False

        self._setup_ui()

    def _setup_ui(self):
        widget = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(6)

        # ===================== CLASS SELECTOR =====================
        class_group = QGroupBox("Class")
        class_layout = QHBoxLayout()
        self._class_combo = QComboBox()
        class_layout.addWidget(self._class_combo)
        class_group.setLayout(class_layout)
        layout.addWidget(class_group)

        # ===================== REGIONS =====================
        region_group = QGroupBox("Regions")
        region_layout = QVBoxLayout()

        self._region_list = QListWidget()
        self._region_list.setMaximumHeight(120)
        self._region_list.setToolTip(
            "Exhaustive labeling regions. Everything inside a region "
            "that is not annotated is treated as background."
        )
        region_layout.addWidget(self._region_list)

        add_region_layout = QHBoxLayout()
        self._add_region_btn = QPushButton("Add Region")
        self._add_region_btn.setToolTip(
            "Draw a new exhaustive labeling region on the map. "
            "Left-click to add points, right-click to finish."
        )
        self._add_region_btn.clicked.connect(
            lambda: self.add_region_requested.emit()
        )
        add_region_layout.addWidget(self._add_region_btn)

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setToolTip("Sync regions and annotations from backend")
        self._refresh_btn.clicked.connect(self._on_refresh)
        add_region_layout.addWidget(self._refresh_btn)
        region_layout.addLayout(add_region_layout)

        region_btn_layout = QHBoxLayout()
        self._zoom_region_btn = QPushButton("Zoom To")
        self._zoom_region_btn.setToolTip("Zoom map to selected region")
        self._zoom_region_btn.clicked.connect(self._on_zoom_region)
        region_btn_layout.addWidget(self._zoom_region_btn)

        self._delete_region_btn = QPushButton("Delete Region")
        self._delete_region_btn.setToolTip(
            "Delete selected region and ALL its annotations"
        )
        self._delete_region_btn.clicked.connect(self._on_delete_region)
        self._delete_region_btn.setStyleSheet("color: #d32f2f;")
        region_btn_layout.addWidget(self._delete_region_btn)

        region_layout.addLayout(region_btn_layout)
        region_group.setLayout(region_layout)
        layout.addWidget(region_group)

        # ===================== ANNOTATIONS =====================
        ann_group = QGroupBox("Annotations")
        ann_layout = QVBoxLayout()

        self._ann_count_label = QLabel("0 annotations")
        ann_layout.addWidget(self._ann_count_label)

        ann_btn_layout = QHBoxLayout()
        self._delete_ann_btn = QPushButton("Delete Selected Annotation")
        self._delete_ann_btn.setToolTip(
            "Click an annotation on the map, then press this to delete it"
        )
        self._delete_ann_btn.clicked.connect(self._on_delete_annotation)
        self._delete_ann_btn.setStyleSheet("color: #d32f2f;")
        ann_btn_layout.addWidget(self._delete_ann_btn)

        self._clear_region_ann_btn = QPushButton("Clear Region Anns")
        self._clear_region_ann_btn.setToolTip(
            "Delete all annotations in the selected region (keeps region)"
        )
        self._clear_region_ann_btn.clicked.connect(self._on_clear_region_annotations)
        self._clear_region_ann_btn.setStyleSheet("color: #d32f2f;")
        ann_btn_layout.addWidget(self._clear_region_ann_btn)

        ann_layout.addLayout(ann_btn_layout)
        ann_group.setLayout(ann_layout)
        layout.addWidget(ann_group)

        # ===================== SAM3 SESSION =====================
        sam_group = QGroupBox("SAM3 Interactive")
        sam_layout = QVBoxLayout()

        # Capture
        capture_layout = QHBoxLayout()
        self._capture_btn = QPushButton("Capture && Set Image")
        self._capture_btn.setToolTip("Capture visible extent and load into SAM3")
        self._capture_btn.clicked.connect(self._on_capture)
        capture_layout.addWidget(self._capture_btn)

        self._reset_btn = QPushButton("Reset Session")
        self._reset_btn.clicked.connect(self._on_sam_reset)
        self._reset_btn.setEnabled(False)
        capture_layout.addWidget(self._reset_btn)
        sam_layout.addLayout(capture_layout)

        # Status
        self._status_label = QLabel("No active session")
        sam_layout.addWidget(self._status_label)

        # Mode
        mode_layout = QHBoxLayout()
        mode_layout.addWidget(QLabel("Mode:"))
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["Point Click", "Box Draw"])
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_layout.addWidget(self._mode_combo)
        sam_layout.addLayout(mode_layout)

        # Accept/Reject
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
        sam_layout.addLayout(action_layout)

        self._score_label = QLabel("")
        sam_layout.addWidget(self._score_label)

        sam_group.setLayout(sam_layout)
        layout.addWidget(sam_group)

        layout.addStretch()
        widget.setLayout(layout)
        self.setWidget(widget)

    # ===================== PUBLIC API =====================

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
        """Refresh region list from backend (with annotation counts)."""
        self._region_list.clear()
        try:
            crs = self.iface.mapCanvas().mapSettings().destinationCrs().authid()
            regions = self.client.get_regions(crs=crs)
            annotations = self.client.get_annotations(crs=crs)
        except Exception:
            return

        # Count annotations per region
        counts: dict[int, int] = {}
        for ann in annotations:
            rid = ann.get("region_id", 0)
            counts[rid] = counts.get(rid, 0) + 1

        total_ann = len(annotations)

        for r in regions:
            rid = r["region_id"]
            count = counts.get(rid, 0)
            item = QListWidgetItem(f"Region {rid}  ({count} annotations)")
            item.setData(Qt.UserRole, rid)
            self._region_list.addItem(item)

        self._ann_count_label.setText(f"{total_ann} annotations total")

    def get_active_class_id(self) -> int:
        data = self._class_combo.currentData()
        return data if data is not None else 2

    def get_active_region_id(self) -> int:
        item = self._region_list.currentItem()
        if item is not None:
            return item.data(Qt.UserRole)
        # Fall back to first region
        if self._region_list.count() > 0:
            return self._region_list.item(0).data(Qt.UserRole)
        return 1

    def get_mode(self) -> str:
        return self._current_mode

    def set_mask_available(self, available: bool, score: float = 0.0):
        self._accept_btn.setEnabled(available)
        self._reject_btn.setEnabled(available)
        if available:
            self._score_label.setText(f"Mask score: {score:.3f}")
        else:
            self._score_label.setText("")

    def set_session_active(self, active: bool, info: str = ""):
        self._session_active = active
        self._reset_btn.setEnabled(active)
        if active:
            self._status_label.setText(f"Session active: {info}")
        else:
            self._status_label.setText("No active session")
            self.set_mask_available(False)

    # ===================== REGION ACTIONS =====================

    def _on_refresh(self):
        """Refresh regions, annotations, and QGIS layers."""
        self.refresh_classes()
        self.refresh_regions()
        self.layers_changed.emit()

    def _on_delete_region(self):
        """Delete selected region and its annotations."""
        item = self._region_list.currentItem()
        if item is None:
            self.iface.messageBar().pushMessage(
                PLUGIN_NAME, "Select a region first", level=1, duration=3
            )
            return

        region_id = item.data(Qt.UserRole)
        reply = QMessageBox.question(
            self,
            "Delete Region",
            f"Delete Region {region_id} and ALL its annotations?\n"
            "This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            result = self.client.delete_region(region_id)
            deleted = result.get("annotations_deleted", 0)
            self.iface.messageBar().pushMessage(
                PLUGIN_NAME,
                f"Deleted region {region_id} and {deleted} annotations",
                level=0,
                duration=3,
            )
            self.refresh_regions()
            self.layers_changed.emit()
        except Exception as e:
            self.iface.messageBar().pushMessage(
                PLUGIN_NAME, f"Delete failed: {e}", level=2, duration=5
            )

    def _on_zoom_region(self):
        """Zoom to the selected region on the map."""
        item = self._region_list.currentItem()
        if item is None:
            return

        region_id = item.data(Qt.UserRole)
        try:
            crs = self.iface.mapCanvas().mapSettings().destinationCrs().authid()
            regions = self.client.get_regions(crs=crs)
            for r in regions:
                if r["region_id"] == region_id:
                    geom = r["geometry"]
                    coords = geom.get("coordinates", [[]])
                    if coords and coords[0]:
                        from qgis.core import QgsRectangle
                        xs = [c[0] for c in coords[0]]
                        ys = [c[1] for c in coords[0]]
                        rect = QgsRectangle(min(xs), min(ys), max(xs), max(ys))
                        rect.scale(1.1)
                        self.iface.mapCanvas().setExtent(rect)
                        self.iface.mapCanvas().refresh()
                    break
        except Exception as e:
            logger.warning("Zoom to region failed: %s", e)

    # ===================== ANNOTATION ACTIONS =====================

    def _on_delete_annotation(self):
        """Delete the currently selected annotation from the QGIS layer."""
        # Get selected feature from annotation layer
        from .label_layer import LabelLayerManager
        project = __import__("qgis.core", fromlist=["QgsProject"]).QgsProject.instance()

        ann_layer = None
        for layer in project.mapLayers().values():
            if layer.name() == LabelLayerManager.ANNOTATIONS_LAYER_NAME:
                ann_layer = layer
                break

        if ann_layer is None:
            self.iface.messageBar().pushMessage(
                PLUGIN_NAME, "No annotation layer found. Click Refresh first.",
                level=1, duration=3,
            )
            return

        selected = ann_layer.selectedFeatures()
        if not selected:
            self.iface.messageBar().pushMessage(
                PLUGIN_NAME,
                "Select an annotation on the map first (use the selection tool)",
                level=1, duration=3,
            )
            return

        for feat in selected:
            ann_idx = feat.attribute("annotation_index")
            try:
                self.client.delete_annotation(ann_idx)
                self.iface.messageBar().pushMessage(
                    PLUGIN_NAME,
                    f"Deleted annotation {ann_idx}",
                    level=0, duration=2,
                )
            except Exception as e:
                self.iface.messageBar().pushMessage(
                    PLUGIN_NAME, f"Delete failed: {e}", level=2, duration=5
                )

        self.refresh_regions()
        self.layers_changed.emit()

    def _on_clear_region_annotations(self):
        """Delete all annotations in the selected region."""
        item = self._region_list.currentItem()
        if item is None:
            self.iface.messageBar().pushMessage(
                PLUGIN_NAME, "Select a region first", level=1, duration=3
            )
            return

        region_id = item.data(Qt.UserRole)
        reply = QMessageBox.question(
            self,
            "Clear Annotations",
            f"Delete ALL annotations in Region {region_id}?\n"
            "The region itself will be kept.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            result = self.client.delete_region_annotations(region_id)
            deleted = result.get("deleted", 0)
            self.iface.messageBar().pushMessage(
                PLUGIN_NAME,
                f"Deleted {deleted} annotations from region {region_id}",
                level=0, duration=3,
            )
            self.refresh_regions()
            self.layers_changed.emit()
        except Exception as e:
            self.iface.messageBar().pushMessage(
                PLUGIN_NAME, f"Delete failed: {e}", level=2, duration=5
            )

    # ===================== SAM3 CONTROLS =====================

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
            self.set_session_active(
                True, f"{session_id} ({img_size[0]}x{img_size[1]})"
            )
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

    def _on_sam_reset(self):
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
            self.client.sam_accept(
                class_id=self.get_active_class_id(),
                region_id=self.get_active_region_id(),
                crs=crs,
            )
            self.iface.messageBar().pushMessage(
                PLUGIN_NAME, "Annotation saved (SAM3)", level=0, duration=2
            )
            self.set_mask_available(False)
            self.mask_accepted.emit()
            # Refresh layers to show the new annotation
            self.refresh_regions()
            self.layers_changed.emit()
        except Exception as e:
            msg = str(e)
            # Check for outside-region error from backend
            if "outside region" in msg.lower():
                self.iface.messageBar().pushMessage(
                    PLUGIN_NAME,
                    "Annotation rejected: outside the selected region. "
                    "Check your region selection.",
                    level=2, duration=5,
                )
            else:
                self.iface.messageBar().pushMessage(
                    PLUGIN_NAME, f"Save failed: {e}", level=2, duration=5
                )

    def _on_reject(self):
        self.set_mask_available(False)
        self.mask_rejected.emit()
