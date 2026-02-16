"""QgsMapTool for drawing annotation region polygons.

The user draws a polygon on the map canvas. When complete, it's sent
to the backend as an annotation region and added to the local region layer.
"""

from __future__ import annotations

from qgis.core import QgsGeometry, QgsPointXY, QgsWkbTypes
from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.PyQt.QtCore import Qt, pyqtSignal, QObject
from qgis.PyQt.QtGui import QColor


class _RegionToolSignals(QObject):
    region_created = pyqtSignal()


class RegionTool(QgsMapTool):
    """Map tool for drawing annotation region polygons."""

    def __init__(self, canvas, client):
        super().__init__(canvas)
        self.client = client
        self._rubber_band = None
        self._points = []
        self._signals = _RegionToolSignals()

    @property
    def region_created(self):
        return self._signals.region_created

    def activate(self) -> None:
        super().activate()
        self._reset()
        self.canvas().setCursor(Qt.CrossCursor)

    def deactivate(self) -> None:
        self._cleanup()
        super().deactivate()

    def canvasPressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            point = self.toMapCoordinates(event.pos())
            self._points.append(point)
            self._update_rubber_band()

        elif event.button() == Qt.RightButton:
            # Complete the polygon
            if len(self._points) >= 3:
                self._finalize()

    def canvasMoveEvent(self, event) -> None:
        if self._points:
            point = self.toMapCoordinates(event.pos())
            self._update_rubber_band(preview_point=point)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self._reset()

    def _update_rubber_band(self, preview_point=None) -> None:
        if not self._rubber_band:
            self._rubber_band = QgsRubberBand(self.canvas(), QgsWkbTypes.PolygonGeometry)
            self._rubber_band.setColor(QColor(255, 165, 0, 40))
            self._rubber_band.setStrokeColor(QColor(255, 165, 0, 200))
            self._rubber_band.setWidth(2)
            self._rubber_band.setLineStyle(Qt.DashLine)

        self._rubber_band.reset(QgsWkbTypes.PolygonGeometry)
        for point in self._points:
            self._rubber_band.addPoint(point)
        if preview_point is not None:
            self._rubber_band.addPoint(preview_point)

    def _finalize(self) -> None:
        """Complete the region polygon and send to backend."""
        if len(self._points) < 3:
            return

        # Create geometry
        geometry = QgsGeometry.fromPolygonXY([self._points + [self._points[0]]])

        # Convert to GeoJSON for backend
        crs = self.canvas().mapSettings().destinationCrs().authid()
        geojson = {
            "type": "Polygon",
            "coordinates": [
                [[p.x(), p.y()] for p in self._points] + [[self._points[0].x(), self._points[0].y()]]
            ],
        }

        try:
            result = self.client.add_region(geojson, crs=crs)
            region_id = result.get("region_id", 0)

            # Show message
            from qgis.utils import iface
            iface.messageBar().pushMessage(
                "HITL Sketcher",
                f"Annotation region {region_id} created",
                level=0,
                duration=3,
            )
            self._signals.region_created.emit()
        except Exception as e:
            from qgis.utils import iface
            iface.messageBar().pushMessage(
                "HITL Sketcher",
                f"Failed to create region: {e}",
                level=2,
                duration=5,
            )

        self._reset()

    def _reset(self) -> None:
        self._points = []
        if self._rubber_band:
            self._rubber_band.reset()

    def _cleanup(self) -> None:
        self._reset()
        if self._rubber_band:
            self.canvas().scene().removeItem(self._rubber_band)
            self._rubber_band = None
