"""QgsMapTool for SAM3 interactive segmentation.

Supports two modes:
- Point click: left-click = foreground point, right-click = background point.
  Each click sends accumulated points to SAM3 and updates the mask overlay.
- Box draw: click-drag to draw a bounding box prompt.

The mask is displayed as a semi-transparent image overlay on the canvas.
The user accepts/rejects via the SAM panel.
"""

from __future__ import annotations

import base64
import logging
from typing import List, Optional, Tuple

from qgis.core import (
    QgsGeometry,
    QgsPointXY,
    QgsRectangle,
    QgsWkbTypes,
)
from qgis.gui import QgsMapCanvasItem, QgsMapTool, QgsRubberBand
from qgis.PyQt.QtCore import QByteArray, QRectF, Qt
from qgis.PyQt.QtGui import QColor, QImage, QPainter

logger = logging.getLogger(__name__)


class MaskOverlay(QgsMapCanvasItem):
    """Canvas item that renders a mask image as a semi-transparent overlay."""

    def __init__(self, canvas):
        super().__init__(canvas)
        self._canvas = canvas
        self._mask_image: Optional[QImage] = None
        self._extent: Optional[QgsRectangle] = None
        self.setZValue(50)  # above map layers, below rubber bands

    def set_mask(self, mask_image: QImage, extent: QgsRectangle):
        """Set the mask to display."""
        self._mask_image = mask_image
        self._extent = extent
        self.update()

    def clear(self):
        self._mask_image = None
        self._extent = None
        self.update()

    def _get_canvas_rect(self) -> QRectF:
        if self._extent is None:
            return QRectF()
        tl = self.toCanvasCoordinates(
            QgsPointXY(self._extent.xMinimum(), self._extent.yMaximum())
        )
        br = self.toCanvasCoordinates(
            QgsPointXY(self._extent.xMaximum(), self._extent.yMinimum())
        )
        return QRectF(tl, br)

    def paint(self, painter: QPainter, option=None, widget=None):
        if self._mask_image is None or self._extent is None:
            return
        rect = self._get_canvas_rect()
        painter.setOpacity(0.45)
        painter.drawImage(rect, self._mask_image)

    def boundingRect(self) -> QRectF:
        if self._extent is None:
            return QRectF()
        return self._get_canvas_rect()

    def updatePosition(self):
        """Called by QGIS when the canvas is panned/zoomed."""
        if self._extent is not None:
            self.update()


class SAMTool(QgsMapTool):
    """Map tool for SAM3 interactive labeling.

    Click on the map canvas to send point prompts to SAM3. The returned
    mask is displayed as an image overlay. Supports iterative refinement
    with positive (left-click) and negative (right-click) points.
    """

    def __init__(self, canvas, client, sam_panel=None):
        super().__init__(canvas)
        self.client = client
        self.sam_panel = sam_panel

        # Overlay and rubber bands
        self._mask_overlay: Optional[MaskOverlay] = None
        self._fg_bands: List[QgsRubberBand] = []  # green foreground markers
        self._bg_bands: List[QgsRubberBand] = []  # red background markers
        self._box_band: Optional[QgsRubberBand] = None  # box drawing

        # State
        self._mode = "click"  # "click" or "box"
        self._click_points: List[Tuple[QgsPointXY, int]] = []  # (point, label)
        self._box_start: Optional[QgsPointXY] = None
        self._drawing_box = False
        self._needs_reset = False  # send reset_prompts on next prompt

        # Geo-to-pixel transform info (set when session starts)
        self._image_extent: Optional[QgsRectangle] = None
        self._image_width: int = 0
        self._image_height: int = 0

    def set_mode(self, mode: str):
        """Set interaction mode: 'click' or 'box'."""
        self._mode = mode
        self._clear_prompts()

    def set_image_info(self, extent: QgsRectangle, width: int, height: int):
        """Set the geo-extent and pixel dimensions of the SAM3 image."""
        self._image_extent = extent
        self._image_width = width
        self._image_height = height

    def activate(self) -> None:
        super().activate()
        self._clear_prompts()
        self.canvas().setCursor(Qt.CrossCursor)
        if self.sam_panel:
            self.sam_panel.mode_changed.connect(self.set_mode)
            self.sam_panel.mask_accepted.connect(self._on_mask_accepted)
            self.sam_panel.mask_rejected.connect(self._on_mask_rejected)

    def deactivate(self) -> None:
        self._cleanup()
        if self.sam_panel:
            try:
                self.sam_panel.mode_changed.disconnect(self.set_mode)
                self.sam_panel.mask_accepted.disconnect(self._on_mask_accepted)
                self.sam_panel.mask_rejected.disconnect(self._on_mask_rejected)
            except TypeError:
                pass
        super().deactivate()

    def canvasPressEvent(self, event) -> None:
        if self._mode == "click":
            self._handle_click(event)
        elif self._mode == "box":
            if event.button() == Qt.LeftButton:
                self._box_start = self.toMapCoordinates(event.pos())
                self._drawing_box = True

    def canvasMoveEvent(self, event) -> None:
        if self._mode == "box" and self._drawing_box and self._box_start:
            current = self.toMapCoordinates(event.pos())
            self._update_box_band(self._box_start, current)

    def canvasReleaseEvent(self, event) -> None:
        if self._mode == "box" and self._drawing_box and event.button() == Qt.LeftButton:
            self._drawing_box = False
            end = self.toMapCoordinates(event.pos())
            if self._box_start:
                self._send_box_prompt(self._box_start, end)
            self._box_start = None

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self._clear_prompts()
            if self.sam_panel:
                self.sam_panel.set_mask_available(False)
        elif event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
            if self.sam_panel:
                self.sam_panel._on_accept()

    # --- Click mode ---

    def _handle_click(self, event):
        """Handle a point click: left=foreground, right=background."""
        point = self.toMapCoordinates(event.pos())

        if self._image_extent is None or self._image_width == 0:
            from qgis.utils import iface
            iface.messageBar().pushMessage(
                "HITL Sketcher",
                "No SAM3 image loaded. Click 'Capture & Set Image' in the Project panel first.",
                level=1, duration=5,
            )
            return

        pixel = self._map_to_pixel(point)
        if pixel is None:
            from qgis.utils import iface
            iface.messageBar().pushMessage(
                "HITL Sketcher",
                "Click is outside the captured image extent. Capture a new region first.",
                level=1, duration=3,
            )
            return

        if event.button() == Qt.LeftButton:
            label = 1  # foreground
        elif event.button() == Qt.RightButton:
            label = 0  # background
        else:
            return

        self._click_points.append((point, label))
        self._add_point_marker(point, label)
        self._send_point_prompt(pixel, label)

    def _send_point_prompt(self, pixel_coord, label):
        """Send point prompt to SAM3 backend."""
        reset = self._needs_reset
        self._needs_reset = False

        try:
            result = self.client.sam_prompt(
                point_coords=[[pixel_coord[0], pixel_coord[1]]],
                point_labels=[label],
                reset_prompts=reset,
            )
            self._show_mask(result)
        except Exception as e:
            from qgis.utils import iface
            iface.messageBar().pushMessage(
                "HITL Sketcher", f"SAM3 prompt failed: {e}", level=2, duration=5,
            )

    # --- Box mode ---

    def _send_box_prompt(self, start: QgsPointXY, end: QgsPointXY):
        """Send box prompt to SAM3 backend."""
        p1 = self._map_to_pixel(start)
        p2 = self._map_to_pixel(end)
        if p1 is None or p2 is None:
            return

        box = [
            min(p1[0], p2[0]),
            min(p1[1], p2[1]),
            max(p1[0], p2[0]),
            max(p1[1], p2[1]),
        ]

        try:
            result = self.client.sam_prompt(box=box, reset_prompts=True)
            self._show_mask(result)
        except Exception as e:
            from qgis.utils import iface
            iface.messageBar().pushMessage(
                "HITL Sketcher", f"SAM3 box prompt failed: {e}", level=2, duration=5,
            )

    # --- Mask display ---

    def _show_mask(self, result: dict):
        """Display the SAM3 mask result as an image overlay."""
        score = result.get("score", 0)
        mask_png = result.get("mask_png")

        if mask_png and self._image_extent:
            self._update_mask_overlay(mask_png)

        if self.sam_panel:
            self.sam_panel.set_mask_available(True, score)

    def _update_mask_overlay(self, mask_png_b64: str):
        """Render the mask as a colored semi-transparent image overlay."""
        try:
            mask_bytes = base64.b64decode(mask_png_b64)

            ba = QByteArray(mask_bytes)
            gray = QImage()
            gray.loadFromData(ba, "PNG")

            if gray.isNull():
                return

            w, h = gray.width(), gray.height()

            # Convert grayscale mask to RGBA: blue where mask=1, transparent elsewhere
            colored = QImage(w, h, QImage.Format_ARGB32)
            colored.fill(Qt.transparent)
            mask_color = QColor(0, 120, 255, 160).rgba()
            for y in range(h):
                for x in range(w):
                    pixel = gray.pixel(x, y) & 0xFF
                    if pixel > 127:
                        colored.setPixel(x, y, mask_color)

            # Create or update overlay
            if self._mask_overlay is None:
                self._mask_overlay = MaskOverlay(self.canvas())

            self._mask_overlay.set_mask(colored, self._image_extent)
            self._mask_overlay.show()

        except Exception as e:
            logger.warning("Mask overlay failed: %s", e)

    # --- Coordinate transforms ---

    def _map_to_pixel(self, point: QgsPointXY):
        """Convert map coordinates to pixel coordinates in the SAM3 image."""
        if self._image_extent is None or self._image_width == 0:
            return None

        ext = self._image_extent
        if not ext.contains(point):
            return None

        px = (point.x() - ext.xMinimum()) / ext.width() * self._image_width
        py = (ext.yMaximum() - point.y()) / ext.height() * self._image_height

        return [px, py]

    def _pixel_to_map(self, px: float, py: float):
        """Convert pixel coordinates to map coordinates."""
        if self._image_extent is None or self._image_width == 0:
            return None

        ext = self._image_extent
        x = ext.xMinimum() + (px / self._image_width) * ext.width()
        y = ext.yMaximum() - (py / self._image_height) * ext.height()

        return [x, y]

    # --- Visual helpers ---

    def _add_point_marker(self, point: QgsPointXY, label: int):
        """Add a single point marker with the correct color."""
        band = QgsRubberBand(self.canvas(), QgsWkbTypes.PointGeometry)
        band.setIconSize(10)
        band.setWidth(3)
        if label == 1:
            band.setColor(QColor(0, 255, 0, 200))  # green = foreground
            self._fg_bands.append(band)
        else:
            band.setColor(QColor(255, 0, 0, 200))  # red = background
            self._bg_bands.append(band)
        band.addPoint(point)

    def _update_box_band(self, start: QgsPointXY, end: QgsPointXY):
        """Show box drawing rectangle."""
        if not self._box_band:
            self._box_band = QgsRubberBand(self.canvas(), QgsWkbTypes.PolygonGeometry)
            self._box_band.setColor(QColor(255, 165, 0, 40))
            self._box_band.setStrokeColor(QColor(255, 165, 0, 200))
            self._box_band.setWidth(2)

        rect = QgsRectangle(start, end)
        self._box_band.setToGeometry(QgsGeometry.fromRect(rect), None)

    # --- State management ---

    def _on_mask_accepted(self):
        """Called when user accepts the mask — reset prompts for next object."""
        self._clear_prompts()

    def _on_mask_rejected(self):
        """Called when user rejects the mask — clear local + flag backend reset."""
        self._clear_prompts()
        self._needs_reset = True

    def _clear_prompts(self):
        """Clear all click points and mask overlay.

        Rubber bands are hidden but NOT removed from the scene — removing
        a QGraphicsItem while a QGraphicsView::paintEvent() is queued causes
        SIGSEGV.  Hidden items are kept alive and only destroyed on unload.
        """
        self._click_points = []
        self._box_start = None
        self._drawing_box = False

        if self._mask_overlay:
            self._mask_overlay.clear()

        for band in self._fg_bands + self._bg_bands:
            band.hide()
        self._fg_bands = []
        self._bg_bands = []

        if self._box_band:
            self._box_band.reset()

    def _cleanup(self):
        """Full cleanup on deactivation.

        Canvas items are hidden but NOT removed from the scene — removing
        a QGraphicsItem while a QGraphicsView::paintEvent() is queued causes
        SIGSEGV.  Hidden items are kept alive and only destroyed on unload.
        """
        if self._mask_overlay:
            self._mask_overlay.hide()

        if self._box_band:
            self._box_band.hide()

        self._clear_prompts()

    def destroy(self):
        """Remove all canvas items from the scene.

        Safe to call ONLY during plugin unload when no more paint events
        will fire.  During normal operation use _cleanup() (hide-only).
        """
        scene = self.canvas().scene()
        if scene is None:
            return

        if self._mask_overlay:
            scene.removeItem(self._mask_overlay)
            self._mask_overlay = None

        for band in self._fg_bands + self._bg_bands:
            scene.removeItem(band)
        self._fg_bands = []
        self._bg_bands = []

        if self._box_band:
            scene.removeItem(self._box_band)
            self._box_band = None
