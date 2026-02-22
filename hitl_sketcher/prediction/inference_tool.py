"""Inference trigger tool: draw AOI, trigger prediction, poll status.

User draws a rectangle on the map → sends AOI + raster source to backend
→ polls for completion → loads prediction raster as new layer.
"""

from __future__ import annotations

import os
import tempfile
from typing import Optional

from qgis.core import QgsRectangle
from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtGui import QColor


class InferenceTool(QgsMapTool):
    """Map tool for triggering inference on a drawn AOI."""

    def __init__(self, canvas, client, prediction_viewer, iface=None):
        super().__init__(canvas)
        self.client = client
        self.viewer = prediction_viewer
        self._iface = iface
        self._rubber_band = None
        self._start_point = None
        self._poll_timer = None
        self._raster_capture = None

    def activate(self) -> None:
        super().activate()
        self.canvas().setCursor(Qt.CrossCursor)

    def deactivate(self) -> None:
        self._cleanup()
        super().deactivate()

    def canvasPressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._start_point = self.toMapCoordinates(event.pos())
            self._init_rubber_band()

    def canvasMoveEvent(self, event) -> None:
        if self._start_point and self._rubber_band:
            point = self.toMapCoordinates(event.pos())
            self._update_rubber_band(point)

    def canvasReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._start_point:
            end_point = self.toMapCoordinates(event.pos())
            self._trigger_inference(self._start_point, end_point)
            self._start_point = None

    def _init_rubber_band(self) -> None:
        from qgis.core import QgsWkbTypes

        if self._rubber_band:
            self._rubber_band.hide()
        self._rubber_band = QgsRubberBand(self.canvas(), QgsWkbTypes.PolygonGeometry)
        self._rubber_band.setColor(QColor(0, 0, 255, 50))
        self._rubber_band.setStrokeColor(QColor(0, 0, 255, 200))
        self._rubber_band.setWidth(2)

    def _update_rubber_band(self, end_point) -> None:
        from qgis.core import QgsPointXY
        self._rubber_band.reset()
        rect = QgsRectangle(self._start_point, end_point)
        self._rubber_band.addPoint(QgsPointXY(rect.xMinimum(), rect.yMinimum()))
        self._rubber_band.addPoint(QgsPointXY(rect.xMaximum(), rect.yMinimum()))
        self._rubber_band.addPoint(QgsPointXY(rect.xMaximum(), rect.yMaximum()))
        self._rubber_band.addPoint(QgsPointXY(rect.xMinimum(), rect.yMaximum()))
        self._rubber_band.addPoint(QgsPointXY(rect.xMinimum(), rect.yMinimum()))

    def _trigger_inference(self, start, end) -> None:
        """Capture the current viewport as a GeoTIFF and send to backend."""
        from qgis.utils import iface as qgis_iface
        iface = self._iface or qgis_iface

        # Capture current viewport as GeoTIFF
        from ..raster.capture import RasterCapture

        if self._raster_capture is None:
            self._raster_capture = RasterCapture(iface)

        capture_path = self._raster_capture.capture_current_extent()
        if capture_path is None:
            iface.messageBar().pushMessage(
                "HITL Sketcher",
                "Failed to capture raster. Is a raster layer visible?",
                level=2,
                duration=5,
            )
            self._cleanup_rubber_band()
            return

        rect = QgsRectangle(start, end)
        aoi_bounds = [rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum()]

        iface.messageBar().pushMessage(
            "HITL Sketcher",
            "Inference request sent. This may take a while...",
            level=0,
            duration=5,
        )

        try:
            result = self.client.start_inference_upload(
                image_path=capture_path,
                aoi_bounds=aoi_bounds,
            )
            job_id = result.get("job_id", "")
            if job_id:
                self._start_polling(job_id)
        except Exception as e:
            iface.messageBar().pushMessage(
                "HITL Sketcher", f"Inference failed: {e}", level=2, duration=5
            )

        self._cleanup_rubber_band()

    def _start_polling(self, job_id: str) -> None:
        """Poll backend for inference completion."""
        self._poll_timer = QTimer()
        self._poll_timer.timeout.connect(lambda: self._poll_status(job_id))
        self._poll_timer.start(2000)  # poll every 2 seconds

    def _poll_status(self, job_id: str) -> None:
        """Check inference status."""
        try:
            status = self.client.get_inference_status()
            if status.get("status") == "complete":
                self._poll_timer.stop()
                self._download_results(job_id)
            elif status.get("status") == "error":
                self._poll_timer.stop()
                from qgis.utils import iface
                iface.messageBar().pushMessage(
                    "HITL Sketcher",
                    f"Inference error: {status.get('error_message', 'unknown')}",
                    level=2,
                    duration=10,
                )
        except Exception:
            pass  # silently retry on connection errors

    def _download_results(self, job_id: str) -> None:
        """Download and load prediction results."""
        output_dir = tempfile.mkdtemp(prefix="hitl_pred_")

        try:
            # Download class raster
            class_path = self.client.download_prediction(
                job_id, "class_raster", os.path.join(output_dir, "classes.tif")
            )
            # Download confidence
            conf_path = self.client.download_prediction(
                job_id, "confidence_raster", os.path.join(output_dir, "confidence.tif")
            )

            # Load into QGIS
            self.viewer.load_prediction(class_path, conf_path)

            from qgis.utils import iface
            iface.messageBar().pushMessage(
                "HITL Sketcher", "Prediction loaded!", level=0, duration=5
            )
        except Exception as e:
            from qgis.utils import iface
            iface.messageBar().pushMessage(
                "HITL Sketcher", f"Download failed: {e}", level=2, duration=5
            )

    def _cleanup_rubber_band(self) -> None:
        if self._rubber_band:
            self._rubber_band.hide()

    def _cleanup(self) -> None:
        self._cleanup_rubber_band()
        if self._poll_timer:
            self._poll_timer.stop()

    def destroy(self) -> None:
        """Remove canvas items from the scene.

        Safe to call ONLY during plugin unload when no more paint events
        will fire.  During normal operation use _cleanup() (hide-only).
        """
        scene = self.canvas().scene()
        if scene is None:
            return
        if self._rubber_band:
            scene.removeItem(self._rubber_band)
            self._rubber_band = None
        if self._poll_timer:
            self._poll_timer.stop()
