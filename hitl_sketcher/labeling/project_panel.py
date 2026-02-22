"""Unified project panel: project selector, classes, regions, SAM3 controls.

Replaces the separate ClassPanel and LabelingPanel with a single dock widget.
Layout:
  [Project] - combo + create/delete
  [Classes] - list + add/remove + sync
  [Regions] - list + add/refresh/zoom/delete
  [Annotations] - count + delete actions
  [SAM3 Interactive] - capture, mode, accept/reject
"""

from __future__ import annotations

import logging
from typing import Optional

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDockWidget,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..classes.manager import ClassManager

logger = logging.getLogger(__name__)


class ProjectPanel(QDockWidget):
    """Unified dock widget: project, classes, regions, SAM3 controls."""

    # Signals (compatible with LabelingPanel interface)
    mode_changed = pyqtSignal(str)  # "click" or "box"
    mask_accepted = pyqtSignal()
    mask_rejected = pyqtSignal()
    session_started = pyqtSignal(str)  # image path
    layers_changed = pyqtSignal()  # region/annotation added or deleted
    add_region_requested = pyqtSignal()  # user clicked "Add Region"
    polygon_tool_requested = pyqtSignal()  # user clicked "Draw Polygon"
    sam_tool_requested = pyqtSignal()  # user clicked "SAM3 Click/Box"

    def __init__(self, iface, client):
        super().__init__("HITL Project", iface.mainWindow())
        self.iface = iface
        self.client = client
        self.class_manager = ClassManager()
        self._current_mode = "click"
        self._session_active = False

        self._setup_ui()

        # Update labeling status when class or region selection changes
        self._class_combo.currentIndexChanged.connect(self._update_labeling_status)
        self._region_list.currentRowChanged.connect(self._update_labeling_status)

    def _setup_ui(self):
        widget = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(6)

        # ===================== PROJECT SELECTOR =====================
        proj_group = QGroupBox("Project")
        proj_layout = QVBoxLayout()

        self._project_combo = QComboBox()
        self._project_combo.currentIndexChanged.connect(self._on_project_changed)
        proj_layout.addWidget(self._project_combo)

        proj_btn_layout = QHBoxLayout()
        self._create_project_btn = QPushButton("New Project")
        self._create_project_btn.clicked.connect(self._on_create_project)
        proj_btn_layout.addWidget(self._create_project_btn)

        self._delete_project_btn = QPushButton("Delete")
        self._delete_project_btn.clicked.connect(self._on_delete_project)
        self._delete_project_btn.setStyleSheet("color: #d32f2f;")
        proj_btn_layout.addWidget(self._delete_project_btn)

        proj_layout.addLayout(proj_btn_layout)

        self._project_info = QLabel("")
        proj_layout.addWidget(self._project_info)

        proj_group.setLayout(proj_layout)
        layout.addWidget(proj_group)

        # ===================== CLASSES =====================
        class_group = QGroupBox("Classes")
        class_layout = QVBoxLayout()

        # Class selector combo (for active class during labeling)
        self._class_combo = QComboBox()
        class_layout.addWidget(self._class_combo)

        # Add class row
        add_class_layout = QHBoxLayout()
        self._class_name_input = QLineEdit()
        self._class_name_input.setPlaceholderText("Class name")
        add_class_layout.addWidget(self._class_name_input)

        self._add_class_btn = QPushButton("Add")
        self._add_class_btn.clicked.connect(self._on_add_class)
        add_class_layout.addWidget(self._add_class_btn)
        class_layout.addLayout(add_class_layout)

        class_btn_layout = QHBoxLayout()
        self._remove_class_btn = QPushButton("Remove")
        self._remove_class_btn.clicked.connect(self._on_remove_class)
        class_btn_layout.addWidget(self._remove_class_btn)

        self._sync_classes_btn = QPushButton("Sync")
        self._sync_classes_btn.setToolTip("Push local class definitions to backend")
        self._sync_classes_btn.clicked.connect(self._on_sync_classes)
        class_btn_layout.addWidget(self._sync_classes_btn)
        class_layout.addLayout(class_btn_layout)

        self._class_info = QLabel("No classes")
        class_layout.addWidget(self._class_info)

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

        # Active class + region status line
        self._labeling_status = QLabel("Select a class and region to start labeling")
        self._labeling_status.setStyleSheet("color: #666; font-style: italic;")
        ann_layout.addWidget(self._labeling_status)

        # Tool activation buttons
        tool_layout = QHBoxLayout()
        self._polygon_tool_btn = QPushButton("Draw Polygon")
        self._polygon_tool_btn.setCheckable(True)
        self._polygon_tool_btn.setToolTip(
            "Draw a polygon annotation manually. "
            "Left-click to add points, right-click to finish."
        )
        self._polygon_tool_btn.clicked.connect(self._on_polygon_tool)
        tool_layout.addWidget(self._polygon_tool_btn)

        self._sam_tool_btn = QPushButton("SAM3 Click/Box")
        self._sam_tool_btn.setCheckable(True)
        self._sam_tool_btn.setToolTip(
            "SAM3 interactive labeling: left-click=foreground, "
            "right-click=background, or switch to box mode below"
        )
        self._sam_tool_btn.clicked.connect(self._on_sam_tool)
        tool_layout.addWidget(self._sam_tool_btn)
        ann_layout.addLayout(tool_layout)

        self._ann_count_label = QLabel("0 annotations")
        ann_layout.addWidget(self._ann_count_label)

        ann_btn_layout = QHBoxLayout()
        self._delete_ann_btn = QPushButton("Delete Selected")
        self._delete_ann_btn.setToolTip(
            "Click an annotation on the map, then press this to delete it"
        )
        self._delete_ann_btn.clicked.connect(self._on_delete_annotation)
        self._delete_ann_btn.setStyleSheet("color: #d32f2f;")
        ann_btn_layout.addWidget(self._delete_ann_btn)

        self._clear_region_ann_btn = QPushButton("Clear Region")
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

        self._status_label = QLabel("No active session")
        sam_layout.addWidget(self._status_label)

        mode_layout = QHBoxLayout()
        mode_layout.addWidget(QLabel("Mode:"))
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["Point Click", "Box Draw"])
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_layout.addWidget(self._mode_combo)
        sam_layout.addLayout(mode_layout)

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

        scroll = QScrollArea()
        scroll.setWidget(widget)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidget(scroll)

    # ===================== PROJECT ACTIONS =====================

    def refresh_projects(self):
        """Load project list from backend and populate combo."""
        self._project_combo.blockSignals(True)
        current_id = self._project_combo.currentData()
        self._project_combo.clear()

        try:
            projects = self.client.list_projects()
            for p in projects:
                self._project_combo.addItem(p["name"], p["project_id"])

            # Restore selection or select active
            if current_id:
                idx = self._project_combo.findData(current_id)
                if idx >= 0:
                    self._project_combo.setCurrentIndex(idx)
            else:
                # Select the backend's active project
                try:
                    active = self.client.get_active_project()
                    if active.get("active"):
                        pid = active["project"]["project_id"]
                        idx = self._project_combo.findData(pid)
                        if idx >= 0:
                            self._project_combo.setCurrentIndex(idx)
                except Exception:
                    pass
        except Exception as e:
            logger.warning("Failed to load projects: %s", e)

        self._project_combo.blockSignals(False)

    def _on_project_changed(self, index):
        """Switch to the selected project."""
        project_id = self._project_combo.currentData()
        if not project_id:
            return

        try:
            result = self.client.switch_project(project_id)
            proj = result.get("project", {})
            self._project_info.setText(
                f"Active: {proj.get('name', project_id)}"
            )
            # Reload everything for new project
            self.refresh_classes()
            self.refresh_regions()
            self.layers_changed.emit()
        except Exception as e:
            self.iface.messageBar().pushMessage(
                "HITL", f"Switch failed: {e}", level=2, duration=5
            )

    def _on_create_project(self):
        """Create a new project via dialog."""
        name, ok = QInputDialog.getText(
            self, "New Project", "Project name:"
        )
        if not ok or not name.strip():
            return

        # Generate project_id from name
        project_id = name.strip().lower().replace(" ", "_")
        project_id = "".join(c for c in project_id if c.isalnum() or c == "_")

        try:
            self.client.create_project(project_id, name.strip())
            self.refresh_projects()
            # Select the new project
            idx = self._project_combo.findData(project_id)
            if idx >= 0:
                self._project_combo.setCurrentIndex(idx)
            self.iface.messageBar().pushMessage(
                "HITL", f"Created project '{name.strip()}'", level=0, duration=3
            )
        except Exception as e:
            self.iface.messageBar().pushMessage(
                "HITL", f"Create failed: {e}", level=2, duration=5
            )

    def _on_delete_project(self):
        """Delete the currently selected project."""
        project_id = self._project_combo.currentData()
        if not project_id:
            return

        name = self._project_combo.currentText()
        reply = QMessageBox.question(
            self,
            "Delete Project",
            f"Delete project '{name}' and ALL its data?\n"
            "This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            # Must switch away from active project before deleting
            active = self.client.get_active_project()
            if active.get("active") and active.get("project", {}).get("project_id") == project_id:
                # Find another project to switch to
                projects = self.client.list_projects()
                other = [p for p in projects if p["project_id"] != project_id]
                if not other:
                    self.iface.messageBar().pushMessage(
                        "HITL", "Cannot delete the only project.", level=1, duration=5
                    )
                    return
                self.client.switch_project(other[0]["project_id"])

            self.client.delete_project(project_id)
            self.refresh_projects()
            self.refresh_classes()
            self.refresh_regions()
            self.layers_changed.emit()
            self.iface.messageBar().pushMessage(
                "HITL", f"Deleted project '{name}'", level=0, duration=3
            )
        except Exception as e:
            self.iface.messageBar().pushMessage(
                "HITL", f"Delete failed: {e}", level=2, duration=5
            )

    # ===================== CLASS ACTIONS =====================

    def _on_add_class(self):
        name = self._class_name_input.text().strip()
        if not name:
            return
        self.class_manager.add_class(name)
        self._class_name_input.clear()
        self._refresh_class_list()

    def _on_remove_class(self):
        class_id = self._class_combo.currentData()
        if class_id is not None and class_id >= 2:
            self.class_manager.remove_class(class_id)
            self._refresh_class_list()

    def _on_sync_classes(self):
        """Push local class definitions to backend."""
        try:
            self.client.set_classes(self.class_manager.to_dicts())
            self._class_info.setText("Classes synced with backend")
            self.layers_changed.emit()
        except Exception as e:
            self._class_info.setText(f"Sync failed: {e}")

    def _refresh_class_list(self):
        """Update the class combo from local class_manager."""
        prev_id = self._class_combo.currentData()

        self._class_combo.blockSignals(True)
        self._class_combo.clear()
        # Background always first
        self._class_combo.addItem("1: background", 1)

        for cls in self.class_manager.classes:
            self._class_combo.addItem(f"{cls.class_id}: {cls.name}", cls.class_id)

        # Restore previous selection
        if prev_id is not None:
            idx = self._class_combo.findData(prev_id)
            if idx >= 0:
                self._class_combo.setCurrentIndex(idx)
        self._class_combo.blockSignals(False)

        self._class_info.setText(f"{len(self.class_manager.classes)} classes defined")

    def refresh_classes(self):
        """Refresh class list from backend."""
        try:
            classes = self.client.get_classes()
            self.class_manager.from_dicts(classes)
        except Exception:
            pass
        self._refresh_class_list()

    # ===================== PUBLIC API (LabelingPanel compat) =====================

    def refresh_regions(self):
        """Refresh region list from backend (with annotation counts)."""
        # Preserve current selection across rebuild
        prev_rid = None
        cur_item = self._region_list.currentItem()
        if cur_item is not None:
            prev_rid = cur_item.data(Qt.UserRole)

        self._region_list.clear()
        try:
            crs = self.iface.mapCanvas().mapSettings().destinationCrs().authid()
            regions = self.client.get_regions(crs=crs)
            annotations = self.client.get_annotations(crs=crs)
        except Exception:
            return

        counts: dict[int, int] = {}
        for ann in annotations:
            rid = ann.get("region_id", 0)
            counts[rid] = counts.get(rid, 0) + 1

        total_ann = len(annotations)

        restore_row = -1
        for i, r in enumerate(regions):
            rid = r["region_id"]
            count = counts.get(rid, 0)
            item = QListWidgetItem(f"Region {rid}  ({count} annotations)")
            item.setData(Qt.UserRole, rid)
            self._region_list.addItem(item)
            if rid == prev_rid:
                restore_row = i

        # Restore previous selection, or select the last region (most recently created)
        if restore_row >= 0:
            self._region_list.setCurrentRow(restore_row)
        elif self._region_list.count() > 0:
            self._region_list.setCurrentRow(self._region_list.count() - 1)

        self._ann_count_label.setText(f"{total_ann} annotations total")

    def get_active_class_id(self) -> int:
        data = self._class_combo.currentData()
        return data if data is not None else 2

    def get_active_region_id(self) -> Optional[int]:
        """Return the selected region_id, or None if no region exists.

        Auto-selects the first region if none is highlighted.
        """
        item = self._region_list.currentItem()
        if item is not None:
            return item.data(Qt.UserRole)
        if self._region_list.count() > 0:
            self._region_list.setCurrentRow(0)
            return self._region_list.item(0).data(Qt.UserRole)
        return None

    def get_mode(self) -> str:
        return self._current_mode

    def _update_labeling_status(self):
        """Update the status label showing active class + region."""
        class_text = self._class_combo.currentText() or "none"
        region_id = self.get_active_region_id()
        region_text = f"Region {region_id}" if region_id is not None else "no region"
        self._labeling_status.setText(f"Class: {class_text} | {region_text}")
        self._labeling_status.setStyleSheet(
            "color: #333; font-weight: bold;" if region_id is not None else "color: #d32f2f; font-style: italic;"
        )

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
        self.refresh_classes()
        self.refresh_regions()
        self.layers_changed.emit()

    def _on_delete_region(self):
        item = self._region_list.currentItem()
        if item is None:
            self.iface.messageBar().pushMessage(
                "HITL", "Select a region first", level=1, duration=3
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
                "HITL",
                f"Deleted region {region_id} and {deleted} annotations",
                level=0, duration=3,
            )
            self.refresh_regions()
            self.layers_changed.emit()
        except Exception as e:
            self.iface.messageBar().pushMessage(
                "HITL", f"Delete failed: {e}", level=2, duration=5
            )

    def _on_zoom_region(self):
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

    # ===================== TOOL ACTIVATION =====================

    def _on_polygon_tool(self):
        """Activate polygon drawing tool."""
        self._polygon_tool_btn.setChecked(True)
        self._sam_tool_btn.setChecked(False)
        self.polygon_tool_requested.emit()

    def _on_sam_tool(self):
        """Activate SAM3 tool."""
        self._sam_tool_btn.setChecked(True)
        self._polygon_tool_btn.setChecked(False)
        self.sam_tool_requested.emit()

    def deactivate_tool_buttons(self):
        """Uncheck both tool buttons (called when another tool is activated)."""
        self._polygon_tool_btn.setChecked(False)
        self._sam_tool_btn.setChecked(False)

    # ===================== ANNOTATION ACTIONS =====================

    def _on_delete_annotation(self):
        from .label_layer import LabelLayerManager
        from qgis.core import QgsProject

        project = QgsProject.instance()
        ann_layer = None
        for layer in project.mapLayers().values():
            if layer.name() == LabelLayerManager.ANNOTATIONS_LAYER_NAME:
                ann_layer = layer
                break

        if ann_layer is None:
            self.iface.messageBar().pushMessage(
                "HITL", "No annotation layer found. Click Refresh first.",
                level=1, duration=3,
            )
            return

        selected = ann_layer.selectedFeatures()
        if not selected:
            self.iface.messageBar().pushMessage(
                "HITL",
                "Select an annotation on the map first (use the selection tool)",
                level=1, duration=3,
            )
            return

        for feat in selected:
            ann_idx = feat.attribute("annotation_index")
            try:
                self.client.delete_annotation(ann_idx)
                self.iface.messageBar().pushMessage(
                    "HITL", f"Deleted annotation {ann_idx}",
                    level=0, duration=2,
                )
            except Exception as e:
                self.iface.messageBar().pushMessage(
                    "HITL", f"Delete failed: {e}", level=2, duration=5
                )

        self.layers_changed.emit()

    def _on_clear_region_annotations(self):
        item = self._region_list.currentItem()
        if item is None:
            self.iface.messageBar().pushMessage(
                "HITL", "Select a region first", level=1, duration=3
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
                "HITL",
                f"Deleted {deleted} annotations from region {region_id}",
                level=0, duration=3,
            )
            self.layers_changed.emit()
        except Exception as e:
            self.iface.messageBar().pushMessage(
                "HITL", f"Delete failed: {e}", level=2, duration=5
            )

    # ===================== SAM3 CONTROLS =====================

    def _on_capture(self):
        from ..raster.capture import RasterCapture

        if not hasattr(self, '_raster_capture') or self._raster_capture is None:
            self._raster_capture = RasterCapture(self.iface)
        capture = self._raster_capture
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
            # Auto-activate SAM3 tool so user can start clicking immediately
            self._on_sam_tool()
        except Exception as e:
            self.iface.messageBar().pushMessage(
                "HITL", f"SAM3 failed: {e}", level=2, duration=5
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
        region_id = self.get_active_region_id()
        class_id = self.get_active_class_id()

        if region_id is None:
            self.iface.messageBar().pushMessage(
                "HITL",
                "No region selected. Create a region first, then select it.",
                level=2, duration=5,
            )
            return

        try:
            result = self.client.sam_accept(
                class_id=class_id,
                region_id=region_id,
                crs="EPSG:4326",
            )
            class_name = self._class_combo.currentText()
            self.iface.messageBar().pushMessage(
                "HITL",
                f"Annotation saved: {class_name} in Region {region_id}",
                level=0, duration=2,
            )
            self.set_mask_available(False)
            self.mask_accepted.emit()
        except Exception as e:
            msg = str(e)
            if "outside region" in msg.lower():
                self.iface.messageBar().pushMessage(
                    "HITL",
                    f"Mask rejected: centroid is outside Region {region_id}. "
                    "Select the correct region or click inside the region boundary.",
                    level=2, duration=5,
                )
            else:
                self.iface.messageBar().pushMessage(
                    "HITL", f"Save failed: {e}", level=2, duration=5
                )

    def _on_reject(self):
        self.set_mask_available(False)
        self.mask_rejected.emit()
