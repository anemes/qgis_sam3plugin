"""QgsMapTool for drawing inference AOI polygons.

The user draws a polygon on the map canvas (left-click to add vertices,
right-click to finish). The completed polygon is emitted as a signal
for the InferencePanel to consume — this tool does NOT start inference.
"""

from __future__ import annotations

from qgis.core import QgsWkbTypes
from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.PyQt.QtCore import Qt, pyqtSignal, QObject
from qgis.PyQt.QtGui import QColor

from ..labeling.utils import points_to_geojson


class _AOIToolSignals(QObject):
    aoi_drawn = pyqtSignal(dict)


class AOIDrawTool(QgsMapTool):
    """Map tool for drawing inference AOI polygons."""

    def __init__(self, canvas):
        super().__init__(canvas)
        self._rubber_band = None
        self._points = []
        self._signals = _AOIToolSignals()

    @property
    def aoi_drawn(self):
        return self._signals.aoi_drawn

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
            if len(self._points) >= 3:
                self._finalize()

    def canvasMoveEvent(self, event) -> None:
        if self._points:
            point = self.toMapCoordinates(event.pos())
            self._update_rubber_band(preview_point=point)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self._reset()
        elif event.key() == Qt.Key_Backspace and self._points:
            self._points.pop()
            self._update_rubber_band()

    def _update_rubber_band(self, preview_point=None) -> None:
        if not self._rubber_band:
            self._rubber_band = QgsRubberBand(self.canvas(), QgsWkbTypes.PolygonGeometry)
            self._rubber_band.setColor(QColor(0, 100, 255, 40))
            self._rubber_band.setStrokeColor(QColor(0, 100, 255, 200))
            self._rubber_band.setWidth(2)
            self._rubber_band.setLineStyle(Qt.DashLine)

        self._rubber_band.reset(QgsWkbTypes.PolygonGeometry)
        for point in self._points:
            self._rubber_band.addPoint(point)
        if preview_point is not None:
            self._rubber_band.addPoint(preview_point)

    def _finalize(self) -> None:
        """Complete the AOI polygon and emit signal."""
        if len(self._points) < 3:
            return

        geojson = points_to_geojson(self._points)
        self._signals.aoi_drawn.emit(geojson)
        self._reset()

    def _reset(self) -> None:
        self._points = []
        if self._rubber_band:
            self._rubber_band.reset()

    def _cleanup(self) -> None:
        self._reset()
        if self._rubber_band:
            self._rubber_band.hide()

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
