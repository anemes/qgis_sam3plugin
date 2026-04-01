"""QgsMapTool for manual polygon annotation drawing.

The user draws a polygon on the map canvas (left-click to add vertices,
right-click to finish). The polygon is saved as an annotation with the
currently selected class and region.
"""

from __future__ import annotations

from qgis.core import QgsWkbTypes
from qgis.gui import QgsMapTool, QgsRubberBand

from .. import PLUGIN_NAME
from .utils import points_to_geojson
from qgis.PyQt.QtCore import Qt, pyqtSignal, QObject
from qgis.PyQt.QtGui import QColor


class _PolygonToolSignals(QObject):
    annotation_saved = pyqtSignal()


class PolygonTool(QgsMapTool):
    """Map tool for drawing manual annotation polygons."""

    def __init__(self, canvas, client, get_class_id, get_region_id):
        """
        Args:
            canvas: QgsMapCanvas
            client: BackendClient
            get_class_id: callable returning current class_id (int)
            get_region_id: callable returning current region_id (int)
        """
        super().__init__(canvas)
        self.client = client
        self._get_class_id = get_class_id
        self._get_region_id = get_region_id
        self._rubber_band = None
        self._points = []
        self._signals = _PolygonToolSignals()

    @property
    def annotation_saved(self):
        return self._signals.annotation_saved

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
            self._rubber_band.setColor(QColor(0, 180, 255, 60))
            self._rubber_band.setStrokeColor(QColor(0, 180, 255, 220))
            self._rubber_band.setWidth(2)

        self._rubber_band.reset(QgsWkbTypes.PolygonGeometry)
        for point in self._points:
            self._rubber_band.addPoint(point)
        if preview_point is not None:
            self._rubber_band.addPoint(preview_point)

    def _finalize(self) -> None:
        """Complete the polygon and save as annotation."""
        if len(self._points) < 3:
            return

        crs = self.canvas().mapSettings().destinationCrs().authid()
        geojson = points_to_geojson(self._points)

        class_id = self._get_class_id()
        region_id = self._get_region_id()

        if region_id is None:
            from qgis.utils import iface
            iface.messageBar().pushMessage(
                PLUGIN_NAME,
                "No region selected. Create a region first, then select it in the panel.",
                level=2,
                duration=5,
            )
            self._reset()
            return

        try:
            self.client.add_annotation(
                geometry_geojson=geojson,
                class_id=class_id,
                region_id=region_id,
                crs=crs,
                source="manual",
            )
            from qgis.utils import iface
            iface.messageBar().pushMessage(
                PLUGIN_NAME,
                f"Annotation saved (class {class_id}, region {region_id})",
                level=0,
                duration=3,
            )
            self._signals.annotation_saved.emit()
        except Exception as e:
            from qgis.utils import iface
            msg = str(e)
            if "outside region" in msg.lower():
                iface.messageBar().pushMessage(
                    PLUGIN_NAME,
                    f"Polygon rejected: centroid is outside Region {region_id}. "
                    "Draw inside the region or select the correct region.",
                    level=2,
                    duration=5,
                )
            else:
                iface.messageBar().pushMessage(
                    PLUGIN_NAME,
                    f"Failed to save annotation: {e}",
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
