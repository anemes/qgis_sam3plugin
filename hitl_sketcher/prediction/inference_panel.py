"""Inference panel: configure raster source, model, AOI, run inference, view results."""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Optional

from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject
from qgis.PyQt.QtCore import QTimer, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QDockWidget,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QComboBox,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class InferencePanel(QDockWidget):
    """Dock widget for configuring and running inference."""

    draw_aoi_requested = pyqtSignal()
    inference_started = pyqtSignal(str)   # job_id
    inference_complete = pyqtSignal(str)  # job_id
    inference_promoted = pyqtSignal()     # emitted after promote to review succeeds

    def __init__(self, iface, client, viewer):
        super().__init__("Inference", iface.mainWindow())
        self.iface = iface
        self.client = client
        self.viewer = viewer

        self._aoi_geojson: Optional[dict] = None
        self._current_job_id: Optional[str] = None
        self._poll_timer: Optional[QTimer] = None
        self._completed_jobs: list[dict] = []  # panel-local history

        self._setup_ui()

    def _setup_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)

        # --- Raster Source ---
        src_box = QGroupBox("Raster Source")
        src_layout = QVBoxLayout()

        self._source_combo = QComboBox()
        self._source_combo.addItem("(Custom URL)", None)
        self._source_combo.currentIndexChanged.connect(self._on_source_changed)
        src_layout.addWidget(QLabel("Registered source:"))
        src_layout.addWidget(self._source_combo)

        self._xyz_url_input = QLineEdit()
        self._xyz_url_input.setPlaceholderText("https://.../{z}/{x}/{y}.png")
        src_layout.addWidget(QLabel("XYZ tile URL:"))
        src_layout.addWidget(self._xyz_url_input)

        zoom_row = QHBoxLayout()
        zoom_row.addWidget(QLabel("Zoom level:"))
        self._zoom_spin = QSpinBox()
        self._zoom_spin.setRange(1, 22)
        self._zoom_spin.setValue(18)
        zoom_row.addWidget(self._zoom_spin)
        src_layout.addLayout(zoom_row)

        src_btn_row = QHBoxLayout()
        register_btn = QPushButton("Register Source")
        register_btn.clicked.connect(self._on_register_source)
        refresh_src_btn = QPushButton("Refresh")
        refresh_src_btn.clicked.connect(self._refresh_sources)
        src_btn_row.addWidget(register_btn)
        src_btn_row.addWidget(refresh_src_btn)
        src_layout.addLayout(src_btn_row)

        src_box.setLayout(src_layout)
        layout.addWidget(src_box)

        # --- Model ---
        model_box = QGroupBox("Model")
        model_layout = QVBoxLayout()

        self._model_combo = QComboBox()
        model_layout.addWidget(self._model_combo)

        self._model_info = QLabel("No models loaded")
        model_layout.addWidget(self._model_info)

        refresh_model_btn = QPushButton("Refresh Models")
        refresh_model_btn.clicked.connect(self._refresh_models)
        model_layout.addWidget(refresh_model_btn)

        model_box.setLayout(model_layout)
        layout.addWidget(model_box)

        # --- Area of Interest ---
        aoi_box = QGroupBox("Area of Interest")
        aoi_layout = QVBoxLayout()

        self._draw_aoi_btn = QPushButton("Draw AOI")
        self._draw_aoi_btn.setCheckable(True)
        self._draw_aoi_btn.clicked.connect(self._on_draw_aoi)
        aoi_layout.addWidget(self._draw_aoi_btn)

        self._aoi_status = QLabel("No AOI drawn")
        aoi_layout.addWidget(self._aoi_status)

        aoi_note = QLabel("Note: inference runs on the AOI bounding box.")
        aoi_note.setStyleSheet("color: gray; font-size: 10px;")
        aoi_note.setWordWrap(True)
        aoi_layout.addWidget(aoi_note)

        aoi_box.setLayout(aoi_layout)
        layout.addWidget(aoi_box)

        # --- Inference ---
        run_box = QGroupBox("Inference")
        run_layout = QVBoxLayout()

        self._run_btn = QPushButton("Run Inference")
        self._run_btn.clicked.connect(self._on_run_inference)
        run_layout.addWidget(self._run_btn)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setVisible(False)
        run_layout.addWidget(self._progress_bar)

        self._run_status = QLabel("Idle")
        run_layout.addWidget(self._run_status)

        run_box.setLayout(run_layout)
        layout.addWidget(run_box)

        # --- Results ---
        results_box = QGroupBox("Results")
        results_layout = QVBoxLayout()

        self._results_list = QListWidget()
        results_layout.addWidget(self._results_list)

        results_btn_row = QHBoxLayout()
        load_btn = QPushButton("Load Results")
        load_btn.clicked.connect(self._on_load_result)
        remove_btn = QPushButton("Remove Layer")
        remove_btn.setStyleSheet("color: red;")
        remove_btn.clicked.connect(self._on_remove_result)
        results_btn_row.addWidget(load_btn)
        results_btn_row.addWidget(remove_btn)
        results_layout.addLayout(results_btn_row)

        self._promote_btn = QPushButton("Promote to Review")
        self._promote_btn.setToolTip(
            "Promote predictions to in-review annotations.\n"
            "Creates a region from the AOI and imports predictions\n"
            "for review before including in training."
        )
        self._promote_btn.setStyleSheet("background-color: #2196F3; color: white;")
        self._promote_btn.setEnabled(False)
        self._promote_btn.clicked.connect(self._on_promote_inference)
        results_layout.addWidget(self._promote_btn)

        results_box.setLayout(results_layout)
        layout.addWidget(results_box)

        layout.addStretch()
        scroll.setWidget(container)
        self.setWidget(scroll)

        # Enable promote button when a result is selected
        self._results_list.currentRowChanged.connect(self._on_result_selected)

    # --- Raster Source ---

    def _refresh_sources(self) -> None:
        prev_data = self._source_combo.currentData()
        self._source_combo.blockSignals(True)
        self._source_combo.clear()
        self._source_combo.addItem("(Custom URL)", None)
        try:
            sources = self.client.list_raster_sources()
            for s in sources:
                self._source_combo.addItem(
                    f"{s['name']} (z{s.get('default_zoom', '?')})",
                    s,
                )
        except Exception:
            pass
        # Restore selection
        if prev_data is not None:
            sid = prev_data.get("source_id") if isinstance(prev_data, dict) else None
            for i in range(self._source_combo.count()):
                d = self._source_combo.itemData(i)
                if isinstance(d, dict) and d.get("source_id") == sid:
                    self._source_combo.setCurrentIndex(i)
                    break
        self._source_combo.blockSignals(False)

    def _on_source_changed(self, index: int) -> None:
        data = self._source_combo.currentData()
        if isinstance(data, dict):
            self._xyz_url_input.setText(data.get("url_template", ""))
            self._zoom_spin.setValue(data.get("default_zoom", 18))

    def _on_register_source(self) -> None:
        url = self._xyz_url_input.text().strip()
        if not url:
            self.iface.messageBar().pushMessage(
                "Inference", "Enter an XYZ URL first.", level=1, duration=3
            )
            return
        name, ok = QInputDialog.getText(
            self, "Register Source", "Source name:"
        )
        if not ok or not name.strip():
            return
        try:
            self.client.register_xyz_source(
                name=name.strip(),
                url_template=url,
                default_zoom=self._zoom_spin.value(),
            )
            self._refresh_sources()
        except Exception as e:
            self.iface.messageBar().pushMessage(
                "Inference", f"Registration failed: {e}", level=2, duration=5
            )

    # --- Model ---

    def _refresh_models(self) -> None:
        self._model_combo.blockSignals(True)
        prev_run_id = self._model_combo.currentData()
        self._model_combo.clear()
        try:
            result = self.client._get("/api/models/list")
            models = result.get("checkpoints", [])
            production_run = result.get("production_run_id")
            # Dedupe by run_id — keep best mIoU per run
            best_per_run: dict[str, dict] = {}
            for m in models:
                rid = m.get("run_id", "unknown")
                miou = m.get("best_val_mIoU", 0.0)
                if rid not in best_per_run or miou > best_per_run[rid].get("best_val_mIoU", 0.0):
                    best_per_run[rid] = m

            for rid, m in sorted(best_per_run.items()):
                miou = m.get("best_val_mIoU", 0.0)
                star = " *" if rid == production_run else ""
                self._model_combo.addItem(f"{rid} (mIoU: {miou:.3f}){star}", rid)

            count = self._model_combo.count()
            self._model_info.setText(
                f"{count} model{'s' if count != 1 else ''} available"
            )
        except Exception:
            self._model_info.setText("Failed to load models")

        # Restore selection
        if prev_run_id:
            idx = self._model_combo.findData(prev_run_id)
            if idx >= 0:
                self._model_combo.setCurrentIndex(idx)
        self._model_combo.blockSignals(False)

    # --- AOI ---

    def _on_draw_aoi(self) -> None:
        if self._draw_aoi_btn.isChecked():
            self.draw_aoi_requested.emit()

    def set_aoi(self, geojson: dict) -> None:
        """Called when AOIDrawTool emits aoi_drawn."""
        self._aoi_geojson = geojson
        coords = geojson.get("coordinates", [[]])
        n_verts = max(0, len(coords[0]) - 1)  # minus closing vertex
        self._aoi_status.setText(f"AOI set: {n_verts} vertices")
        self._draw_aoi_btn.setChecked(False)

    # --- Inference ---

    def _on_run_inference(self) -> None:
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

        # Compute bounding box from polygon
        coords = self._aoi_geojson["coordinates"][0]
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        aoi_bounds = [min(xs), min(ys), max(xs), max(ys)]

        # Reproject to EPSG:3857 if canvas CRS differs (XYZ tiles are in 3857)
        canvas_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        target_crs = QgsCoordinateReferenceSystem("EPSG:3857")
        if canvas_crs.authid() != "EPSG:3857":
            xform = QgsCoordinateTransform(canvas_crs, target_crs, QgsProject.instance())
            from qgis.core import QgsPointXY
            ll = xform.transform(QgsPointXY(aoi_bounds[0], aoi_bounds[1]))
            ur = xform.transform(QgsPointXY(aoi_bounds[2], aoi_bounds[3]))
            aoi_bounds = [ll.x(), ll.y(), ur.x(), ur.y()]

        run_id = self._model_combo.currentData()
        zoom = self._zoom_spin.value()

        # Capture AOI polygon before starting (may be overwritten before job completes)
        self._pending_aoi = self._aoi_geojson

        try:
            result = self.client.start_inference(
                aoi_bounds=aoi_bounds,
                xyz_url=xyz_url,
                xyz_zoom=zoom,
                checkpoint_run_id=run_id,
            )
            job_id = result.get("job_id", "")
            self._current_job_id = job_id
            self._run_btn.setEnabled(False)
            self._progress_bar.setVisible(True)
            self._progress_bar.setValue(0)
            self._run_status.setText(f"Job {job_id} started...")
            self.inference_started.emit(job_id)
            self._start_polling()
        except Exception as e:
            self._run_status.setText(f"Error: {e}")

    def _start_polling(self) -> None:
        if self._poll_timer:
            self._poll_timer.stop()
        self._poll_timer = QTimer()
        self._poll_timer.timeout.connect(self._poll_status)
        self._poll_timer.start(2000)

    def _poll_status(self) -> None:
        try:
            status = self.client.get_inference_status()
            state = status.get("status", "unknown")
            processed = status.get("tiles_processed", 0)
            total = status.get("tiles_total", 0)
            pct = status.get("progress_pct", 0.0)

            self._progress_bar.setValue(int(pct))
            if total > 0:
                self._run_status.setText(
                    f"Processing tile {processed}/{total} ({pct:.0f}%)"
                )

            if state == "complete":
                self._poll_timer.stop()
                self._on_inference_complete(status)
            elif state == "error":
                self._poll_timer.stop()
                self._run_status.setText(
                    f"Error: {status.get('error_message', 'unknown')}"
                )
                self._run_btn.setEnabled(True)
                self._progress_bar.setVisible(False)
        except Exception:
            pass  # retry on next tick

    def _on_inference_complete(self, status: dict) -> None:
        job_id = status.get("job_id", self._current_job_id or "unknown")
        result_paths = status.get("result_paths", {})

        self._completed_jobs.append({
            "job_id": job_id,
            "result_paths": result_paths,
            "aoi_geojson": getattr(self, "_pending_aoi", None),
        })
        self._results_list.addItem(f"Job: {job_id}")
        self._results_list.setCurrentRow(self._results_list.count() - 1)

        self._run_status.setText(f"Complete: {job_id}")
        self._progress_bar.setValue(100)
        self._run_btn.setEnabled(True)
        self.inference_complete.emit(job_id)

        # Auto-load vector results
        self._load_result(job_id, result_paths)

    def _load_result(self, job_id: str, result_paths: dict) -> None:
        if "vector" not in result_paths:
            self._run_status.setText(f"Complete (no vector output): {job_id}")
            self._progress_bar.setVisible(False)
            return

        output_dir = tempfile.mkdtemp(prefix="hitl_infer_")
        vec_path = os.path.join(output_dir, f"{job_id}_predictions.gpkg")

        try:
            self.client.download_prediction(job_id, "vector", vec_path)
            layer = self.viewer.load_vector_prediction(vec_path, job_id)
            if layer:
                self._run_status.setText(f"Loaded: {job_id}")
            else:
                self._run_status.setText(f"Failed to load layer: {job_id}")
        except Exception as e:
            self._run_status.setText(f"Download failed: {e}")

        self._progress_bar.setVisible(False)

    # --- Results management ---

    def _on_load_result(self) -> None:
        row = self._results_list.currentRow()
        if row < 0 or row >= len(self._completed_jobs):
            return
        job_info = self._completed_jobs[row]
        self._load_result(job_info["job_id"], job_info["result_paths"])

    def _on_remove_result(self) -> None:
        row = self._results_list.currentRow()
        if row < 0 or row >= len(self._completed_jobs):
            return
        job_info = self._completed_jobs[row]
        self.viewer.remove_vector_prediction(job_info["job_id"])
        self._results_list.takeItem(row)
        self._completed_jobs.pop(row)

    # --- Review workflow ---

    def _on_result_selected(self, row: int) -> None:
        self._promote_btn.setEnabled(0 <= row < len(self._completed_jobs))

    @staticmethod
    def _reproject_geojson(geojson: dict, src_crs, dst_crs_id: str) -> dict:
        """Reproject a GeoJSON Polygon geometry between CRS."""
        from qgis.core import QgsPointXY
        dst_crs = QgsCoordinateReferenceSystem(dst_crs_id)
        xform = QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance())
        coords = geojson["coordinates"]
        new_coords = []
        for ring in coords:
            new_ring = []
            for pt in ring:
                transformed = xform.transform(QgsPointXY(pt[0], pt[1]))
                new_ring.append([transformed.x(), transformed.y()])
            new_coords.append(new_ring)
        return {"type": "Polygon", "coordinates": new_coords}

    def _on_promote_inference(self) -> None:
        """Promote selected inference job results to in-review annotations."""
        row = self._results_list.currentRow()
        if row < 0 or row >= len(self._completed_jobs):
            return

        job_info = self._completed_jobs[row]
        job_id = job_info["job_id"]
        aoi_geojson = job_info.get("aoi_geojson")

        if aoi_geojson is None:
            self.iface.messageBar().pushMessage(
                "Inference", "No AOI polygon stored for this job.", level=2, duration=5
            )
            return

        # Reproject AOI from canvas CRS to EPSG:4326 for storage
        canvas_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        if canvas_crs.authid() != "EPSG:4326":
            aoi_geojson = self._reproject_geojson(aoi_geojson, canvas_crs, "EPSG:4326")

        try:
            result = self.client.promote_inference(
                aoi_geojson=aoi_geojson,
                job_id=job_id,
            )
            region_id = result.get("region_id", "?")
            count = result.get("annotations_created", 0)
            self.iface.messageBar().pushMessage(
                "Inference",
                f"Promoted {count} predictions to Region {region_id} (in review)",
                level=0, duration=5,
            )
            self.inference_promoted.emit()
        except Exception as e:
            self.iface.messageBar().pushMessage(
                "Inference", f"Promote failed: {e}", level=2, duration=5
            )
