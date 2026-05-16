"""Standalone inference panel — run any catalogue model on any raster.

This panel is intentionally decoupled from the labeling/training pipeline.
Results go into the 'EasySegment Predictions' layer group and are persisted
in the reserved '_inference' backend project so they survive QGIS restarts.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime
from typing import Optional

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsProject,
)
from qgis.PyQt.QtCore import QSettings, QTimer, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QButtonGroup,
    QDockWidget,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QComboBox,
    QVBoxLayout,
    QWidget,
)

from .. import PLUGIN_NAME
from ..labeling.utils import densify_ring

logger = logging.getLogger(__name__)

_PREDICTIONS_GROUP = f"{PLUGIN_NAME} Predictions"
_SETTINGS_KEY = "hitl_sketcher/standalone_jobs"


class StandaloneInferencePanel(QDockWidget):
    """Dock widget: pick a model from the catalogue and run it on any raster.

    Signals
    -------
    draw_aoi_requested
        Emitted when the user clicks "Draw AOI" in XYZ mode.  The plugin
        should activate an AOIDrawTool and connect its ``aoi_drawn`` signal
        to :meth:`set_aoi`.
    """

    draw_aoi_requested = pyqtSignal()

    def __init__(self, iface, client, viewer, capture):
        super().__init__("Models", iface.mainWindow())
        self.iface = iface
        self.client = client
        self.viewer = viewer
        self.capture = capture  # RasterCapture instance

        self._aoi_geojson: Optional[dict] = None
        self._current_job_id: Optional[str] = None
        self._poll_timer: Optional[QTimer] = None
        self._pending_aoi: Optional[dict] = None

        # Catalogue entry currently selected: {run_id, project_id, display_name, class_names}
        self._selected_model: Optional[dict] = None

        # In-session job history (also persisted to QSettings)
        self._jobs: list[dict] = []

        self._setup_ui()
        self._load_saved_jobs()

    # ── Properties ─────────────────────────────────────────────────────────────

    @property
    def _inference_project(self) -> str:
        """Per-user inference project id, derived from the connected user identity."""
        return f"_inference_{self.client.user_id}"

    # ── UI construction ────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)

        # ── Model ──────────────────────────────────────────────────────────────
        model_box = QGroupBox("Model")
        model_layout = QVBoxLayout()

        self._model_combo = QComboBox()
        self._model_combo.currentIndexChanged.connect(self._on_model_selected)
        model_layout.addWidget(self._model_combo)

        self._model_info = QLabel("Connect to backend to load catalogue")
        self._model_info.setWordWrap(True)
        model_layout.addWidget(self._model_info)

        refresh_btn = QPushButton("Refresh Catalogue")
        refresh_btn.clicked.connect(self.refresh_models)
        model_layout.addWidget(refresh_btn)

        model_box.setLayout(model_layout)
        layout.addWidget(model_box)

        # ── Raster Source ──────────────────────────────────────────────────────
        src_box = QGroupBox("Raster Source")
        src_layout = QVBoxLayout()

        # Radio buttons
        self._radio_xyz = QRadioButton("XYZ Tile URL")
        self._radio_canvas = QRadioButton("Capture Current Canvas Extent")
        self._radio_xyz.setChecked(True)
        radio_group = QButtonGroup(self)
        radio_group.addButton(self._radio_xyz)
        radio_group.addButton(self._radio_canvas)
        self._radio_xyz.toggled.connect(self._on_source_mode_changed)
        src_layout.addWidget(self._radio_xyz)
        src_layout.addWidget(self._radio_canvas)

        # XYZ sub-controls (visible only in XYZ mode)
        self._xyz_widget = QWidget()
        xyz_inner = QVBoxLayout(self._xyz_widget)
        xyz_inner.setContentsMargins(0, 0, 0, 0)

        self._xyz_url_input = QLineEdit()
        self._xyz_url_input.setPlaceholderText("https://.../{z}/{x}/{y}.png")
        xyz_inner.addWidget(QLabel("XYZ tile URL:"))
        xyz_inner.addWidget(self._xyz_url_input)

        zoom_row = QHBoxLayout()
        zoom_row.addWidget(QLabel("Zoom level:"))
        self._zoom_spin = QSpinBox()
        self._zoom_spin.setRange(1, 22)
        self._zoom_spin.setValue(18)
        zoom_row.addWidget(self._zoom_spin)
        zoom_row.addStretch()
        xyz_inner.addLayout(zoom_row)

        src_layout.addWidget(self._xyz_widget)
        src_box.setLayout(src_layout)
        layout.addWidget(src_box)

        # ── Area of Interest (only shown in XYZ mode) ──────────────────────────
        self._aoi_box = QGroupBox("Area of Interest")
        aoi_layout = QVBoxLayout()

        aoi_row = QHBoxLayout()
        self._draw_aoi_btn = QPushButton("Draw AOI")
        self._draw_aoi_btn.setCheckable(True)
        self._draw_aoi_btn.clicked.connect(self._on_draw_aoi)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear_aoi)
        aoi_row.addWidget(self._draw_aoi_btn)
        aoi_row.addWidget(clear_btn)
        aoi_layout.addLayout(aoi_row)

        self._aoi_status = QLabel("No AOI drawn")
        aoi_layout.addWidget(self._aoi_status)

        self._aoi_box.setLayout(aoi_layout)
        layout.addWidget(self._aoi_box)

        # ── Run ────────────────────────────────────────────────────────────────
        run_box = QGroupBox("Run")
        run_layout = QVBoxLayout()

        self._run_btn = QPushButton("Run Inference")
        self._run_btn.clicked.connect(self._on_run)
        run_layout.addWidget(self._run_btn)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setVisible(False)
        run_layout.addWidget(self._progress_bar)

        self._run_status = QLabel("Idle")
        self._run_status.setWordWrap(True)
        run_layout.addWidget(self._run_status)

        run_box.setLayout(run_layout)
        layout.addWidget(run_box)

        # ── Saved Results ──────────────────────────────────────────────────────
        results_box = QGroupBox("Saved Results")
        results_layout = QVBoxLayout()

        self._results_list = QListWidget()
        self._results_list.setToolTip("Click a result to load its layer")
        self._results_list.currentRowChanged.connect(self._on_result_selected)
        results_layout.addWidget(self._results_list)

        results_btn_row = QHBoxLayout()
        self._load_btn = QPushButton("Load Layer")
        self._load_btn.setEnabled(False)
        self._load_btn.clicked.connect(self._on_load_result)
        self._remove_btn = QPushButton("Remove")
        self._remove_btn.setEnabled(False)
        self._remove_btn.setStyleSheet("color: red;")
        self._remove_btn.clicked.connect(self._on_remove_result)
        results_btn_row.addWidget(self._load_btn)
        results_btn_row.addWidget(self._remove_btn)
        results_layout.addLayout(results_btn_row)

        results_box.setLayout(results_layout)
        layout.addWidget(results_box)

        layout.addStretch()
        scroll.setWidget(container)
        self.setWidget(scroll)

    # ── Slot: backend connected ────────────────────────────────────────────────

    def on_connected(self) -> None:
        """Called by plugin when the backend connection is established."""
        self.refresh_models()

    # ── Model catalogue ────────────────────────────────────────────────────────

    def refresh_models(self) -> None:
        """Fetch the global model catalogue and populate the dropdown."""
        self._model_combo.blockSignals(True)
        prev_key = self._model_combo.currentData()
        self._model_combo.clear()
        self._model_combo.addItem("(No model selected — random init)", None)
        try:
            catalogue = self.client.get_model_catalogue()
            for entry in catalogue:
                display = entry.get("display_name") or entry.get("run_id", "unknown")
                self._model_combo.addItem(display, entry)
            count = len(catalogue)
            self._model_info.setText(f"{count} model{'s' if count != 1 else ''} in catalogue")
        except Exception as exc:
            self._model_info.setText(f"Failed to load catalogue: {exc}")

        # Restore previous selection if still available
        if prev_key is not None:
            for i in range(self._model_combo.count()):
                d = self._model_combo.itemData(i)
                if isinstance(d, dict) and d.get("run_id") == prev_key.get("run_id"):
                    self._model_combo.setCurrentIndex(i)
                    break
        self._model_combo.blockSignals(False)
        self._on_model_selected(self._model_combo.currentIndex())

    def _on_model_selected(self, index: int) -> None:
        self._selected_model = self._model_combo.currentData()
        if self._selected_model:
            class_names = self._selected_model.get("class_names", [])
            user_classes = [n for n in class_names[2:] if n]  # skip ignore+background
            self._model_info.setText(
                f"Classes: {', '.join(user_classes) or 'unknown'}"
            )
        else:
            self._model_info.setText("No model selected")

    # ── Raster source mode ─────────────────────────────────────────────────────

    def _on_source_mode_changed(self, xyz_checked: bool) -> None:
        self._xyz_widget.setVisible(xyz_checked)
        self._aoi_box.setVisible(xyz_checked)

    # ── AOI (XYZ mode only) ────────────────────────────────────────────────────

    def _on_draw_aoi(self) -> None:
        if self._draw_aoi_btn.isChecked():
            self.draw_aoi_requested.emit()

    def set_aoi(self, geojson: dict) -> None:
        """Called when AOIDrawTool emits aoi_drawn."""
        self._aoi_geojson = geojson
        coords = geojson.get("coordinates", [[]])
        n_verts = max(0, len(coords[0]) - 1)
        self._aoi_status.setText(f"AOI set: {n_verts} vertices")
        self._draw_aoi_btn.setChecked(False)

    def deactivate_aoi_button(self) -> None:
        """Uncheck the draw button when map tool changes externally."""
        self._draw_aoi_btn.setChecked(False)

    def _clear_aoi(self) -> None:
        self._aoi_geojson = None
        self._aoi_status.setText("No AOI drawn")

    # ── Run inference ──────────────────────────────────────────────────────────

    def _on_run(self) -> None:
        model = self._selected_model  # may be None → random init

        if self._radio_xyz.isChecked():
            self._run_xyz_mode(model)
        else:
            self._run_canvas_mode(model)

    def _run_xyz_mode(self, model: Optional[dict]) -> None:
        """Start inference on an XYZ tile source with a user-drawn AOI."""
        xyz_url = self._xyz_url_input.text().strip()
        if not xyz_url:
            self.iface.messageBar().pushMessage(
                "Inference", "Enter an XYZ tile URL.", level=1, duration=3
            )
            return
        if self._aoi_geojson is None:
            self.iface.messageBar().pushMessage(
                "Inference", "Draw an AOI first.", level=1, duration=3
            )
            return

        # Compute bounding box; reproject to EPSG:3857 if needed
        aoi_bounds = self._geojson_to_bounds_3857(self._aoi_geojson)
        self._pending_aoi = self._aoi_geojson

        try:
            run_id = model.get("run_id") if model else None
            ckpt_project = model.get("project_id") if model else None
            result = self.client.start_inference(
                aoi_bounds=aoi_bounds,
                project_id=self._inference_project,
                checkpoint_run_id=run_id,
                checkpoint_project_id=ckpt_project,
                xyz_url=xyz_url,
                xyz_zoom=self._zoom_spin.value(),
            )
            self._on_inference_started(result)
        except Exception as exc:
            self._run_status.setText(f"Error: {exc}")

    def _run_canvas_mode(self, model: Optional[dict]) -> None:
        """Capture the current canvas as a GeoTIFF and run inference on it."""
        self._run_status.setText("Capturing canvas…")
        captured_path = self.capture.capture_current_extent()
        if not captured_path:
            self.iface.messageBar().pushMessage(
                "Inference", "Canvas capture failed.", level=2, duration=5
            )
            self._run_status.setText("Capture failed.")
            return

        # AOI bounds come from the captured canvas extent (native canvas CRS)
        canvas = self.iface.mapCanvas()
        extent = canvas.extent()
        aoi_bounds = [
            extent.xMinimum(),
            extent.yMinimum(),
            extent.xMaximum(),
            extent.yMaximum(),
        ]

        # Build a GeoJSON polygon from the canvas extent (for display/archival)
        xmin, ymin, xmax, ymax = aoi_bounds
        self._pending_aoi = {
            "type": "Polygon",
            "coordinates": [[
                [xmin, ymin], [xmax, ymin], [xmax, ymax], [xmin, ymax], [xmin, ymin]
            ]],
        }

        try:
            run_id = model.get("run_id") if model else None
            ckpt_project = model.get("project_id") if model else None
            result = self.client.start_inference_upload(
                image_path=captured_path,
                aoi_bounds=aoi_bounds,
                project_id=self._inference_project,
                checkpoint_run_id=run_id,
                checkpoint_project_id=ckpt_project,
            )
            self._on_inference_started(result)
        except Exception as exc:
            self._run_status.setText(f"Error: {exc}")

    def _on_inference_started(self, result: dict) -> None:
        job_id = result.get("job_id", "")
        warnings = result.get("warnings", [])
        self._current_job_id = job_id
        self._run_btn.setEnabled(False)
        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(0)
        self._run_status.setText(f"Job {job_id} started…")
        if warnings:
            logger.info("Inference warnings: %s", warnings)
        self._start_polling()

    # ── Polling ────────────────────────────────────────────────────────────────

    def _start_polling(self) -> None:
        if self._poll_timer:
            self._poll_timer.stop()
        self._poll_timer = QTimer()
        self._poll_timer.timeout.connect(self._poll_status)
        self._poll_timer.start(2000)

    # Human-readable stage labels
    _STAGE_LABELS = {
        "loading_model": "Loading model…",
        "fetching_tiles": "Fetching imagery…",
        "inferring": "Running inference",
        "exporting": "Exporting results…",
    }

    def _poll_status(self) -> None:
        try:
            status = self.client.get_inference_status()
            state = status.get("status", "unknown")
            stage = status.get("stage", "")
            processed = status.get("tiles_processed", 0)
            total = status.get("tiles_total", 0)
            pct = status.get("progress_pct", 0.0)

            if stage in ("loading_model", "fetching_tiles", "exporting"):
                # Indeterminate (pulsing) progress for non-tiled phases
                self._progress_bar.setRange(0, 0)
            else:
                self._progress_bar.setRange(0, 100)
                self._progress_bar.setValue(int(pct))

            if stage == "inferring" and total > 0:
                self._run_status.setText(
                    f"Tile {processed}/{total} ({pct:.0f}%)"
                )
            elif stage in self._STAGE_LABELS:
                self._run_status.setText(self._STAGE_LABELS[stage])

            if state == "complete":
                self._poll_timer.stop()
                self._on_inference_complete(status)
            elif state == "error":
                self._poll_timer.stop()
                msg = status.get("error_message", "unknown error")
                self._run_status.setText(f"Error: {msg}")
                self._run_btn.setEnabled(True)
                self._progress_bar.setVisible(False)
        except Exception:
            pass  # retry on next tick

    # ── Completion ─────────────────────────────────────────────────────────────

    def _on_inference_complete(self, status: dict) -> None:
        job_id = status.get("job_id", self._current_job_id or "unknown")
        result_paths = status.get("result_paths", {})

        model = self._selected_model
        model_name = model.get("display_name", "unknown") if model else "random init"
        class_names = model.get("class_names", []) if model else []
        user_classes = [n for n in class_names[2:] if n]
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        display = f"{timestamp}  {model_name}"

        # Download vector output
        vec_path = self._download_vector(job_id, result_paths)
        if vec_path is None:
            self._run_status.setText(f"Complete (no vector output): {job_id}")
            self._progress_bar.setVisible(False)
            self._run_btn.setEnabled(True)
            return

        # Auto-promote to _inference project for persistence
        aoi_geojson_4326 = self._get_aoi_in_4326()
        if aoi_geojson_4326:
            try:
                self.client.promote_inference(
                    aoi_geojson=aoi_geojson_4326,
                    job_id=job_id,
                    project_id=self._inference_project,
                )
            except Exception as exc:
                logger.warning("Auto-promote to _inference failed: %s", exc)

        # Load as QGIS layer in EasySegment Predictions group
        layer = self.viewer.load_vector_prediction(
            vec_path,
            job_id,
            display_name=display,
            group_name=_PREDICTIONS_GROUP,
        )

        # Persist job metadata so it survives QGIS restarts
        job_record = {
            "job_id": job_id,
            "display_name": display,
            "model_name": model_name,
            "class_names": user_classes,
            "timestamp": timestamp,
            "vec_path": vec_path,
        }
        self._jobs.append(job_record)
        self._save_jobs()
        self._add_result_item(job_record)

        self._run_status.setText(f"Complete: {job_id}")
        self._progress_bar.setValue(100)
        self._run_btn.setEnabled(True)

        if layer:
            self._run_status.setText(f"Loaded: {display}")
        self._progress_bar.setVisible(False)

    def _download_vector(self, job_id: str, result_paths: dict) -> Optional[str]:
        """Download the vector GeoPackage for a completed job."""
        if "vector" not in result_paths:
            return None
        output_dir = tempfile.mkdtemp(prefix="hitl_standalone_")
        vec_path = os.path.join(output_dir, f"{job_id}_predictions.gpkg")
        try:
            self.client.download_prediction(job_id, "vector", vec_path)
            return vec_path
        except Exception as exc:
            logger.error("Vector download failed: %s", exc)
            return None

    def _get_aoi_in_4326(self) -> Optional[dict]:
        """Return the pending AOI reprojected to EPSG:4326 for storage.

        Densifies edges before reprojection so that a rectangle in
        EPSG:3857 reprojects as a smooth curve rather than a trapezoid.
        """
        aoi = getattr(self, "_pending_aoi", None)
        if aoi is None:
            return None
        canvas_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        if canvas_crs.authid() == "EPSG:4326":
            return aoi
        dst_crs = QgsCoordinateReferenceSystem("EPSG:4326")
        xform = QgsCoordinateTransform(canvas_crs, dst_crs, QgsProject.instance())
        new_rings = []
        for ring in aoi.get("coordinates", []):
            dense_ring = densify_ring(ring, max_segment=500)
            new_ring = []
            for pt in dense_ring:
                t = xform.transform(QgsPointXY(pt[0], pt[1]))
                new_ring.append([round(t.x(), 6), round(t.y(), 6)])
            new_rings.append(new_ring)
        return {"type": "Polygon", "coordinates": new_rings}

    # ── Results list ───────────────────────────────────────────────────────────

    def _on_result_selected(self, row: int) -> None:
        has_sel = 0 <= row < len(self._jobs)
        self._load_btn.setEnabled(has_sel)
        self._remove_btn.setEnabled(has_sel)

    def _on_load_result(self) -> None:
        row = self._results_list.currentRow()
        if row < 0 or row >= len(self._jobs):
            return
        job = self._jobs[row]
        vec_path = job.get("vec_path", "")
        if not vec_path or not os.path.exists(vec_path):
            self.iface.messageBar().pushMessage(
                "Inference",
                "Local result file not found. Re-run inference to regenerate.",
                level=1,
                duration=5,
            )
            return
        self.viewer.load_vector_prediction(
            vec_path,
            job["job_id"],
            display_name=job.get("display_name"),
            group_name=_PREDICTIONS_GROUP,
        )

    def _on_remove_result(self) -> None:
        row = self._results_list.currentRow()
        if row < 0 or row >= len(self._jobs):
            return
        job = self._jobs[row]
        self.viewer.remove_vector_prediction(job["job_id"])
        self._results_list.takeItem(row)
        self._jobs.pop(row)
        self._save_jobs()

    def _add_result_item(self, job: dict) -> None:
        item = QListWidgetItem(job.get("display_name", job["job_id"]))
        classes = ", ".join(job.get("class_names", [])) or "—"
        item.setToolTip(f"Classes: {classes}\nJob: {job['job_id']}")
        self._results_list.addItem(item)
        self._results_list.setCurrentRow(self._results_list.count() - 1)

    # ── Persistence (QSettings) ────────────────────────────────────────────────

    def _save_jobs(self) -> None:
        settings = QSettings()
        settings.setValue(_SETTINGS_KEY, json.dumps(self._jobs))

    def _load_saved_jobs(self) -> None:
        settings = QSettings()
        raw = settings.value(_SETTINGS_KEY, "[]")
        try:
            saved = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            saved = []
        for job in saved:
            self._jobs.append(job)
            self._add_result_item(job)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _geojson_to_bounds_3857(self, geojson: dict) -> list:
        """Convert a GeoJSON Polygon (canvas CRS) to EPSG:3857 bounding box."""
        coords = geojson["coordinates"][0]
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        bounds = [min(xs), min(ys), max(xs), max(ys)]

        canvas_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        target_crs = QgsCoordinateReferenceSystem("EPSG:3857")
        if canvas_crs.authid() != "EPSG:3857":
            xform = QgsCoordinateTransform(canvas_crs, target_crs, QgsProject.instance())
            ll = xform.transform(QgsPointXY(bounds[0], bounds[1]))
            ur = xform.transform(QgsPointXY(bounds[2], bounds[3]))
            bounds = [ll.x(), ll.y(), ur.x(), ur.y()]

        return bounds
