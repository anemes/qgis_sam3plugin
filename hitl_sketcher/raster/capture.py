"""Export visible map canvas as GeoTIFF for labeling.

Renders all visible map layers (raster, WMS, XYZ, vector) into a single
composited GeoTIFF using QgsMapRendererCustomPainterJob.  This captures
exactly what the user sees on screen, minus the plugin's own overlay
layers (annotations, regions, rubber bands).
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from osgeo import gdal, osr

from .. import PLUGIN_NAME
from qgis.core import (
    QgsMapRendererCustomPainterJob,
    QgsMapSettings,
    QgsProject,
)
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QImage, QPainter

logger = logging.getLogger(__name__)

# Plugin layer names to exclude from capture
_EXCLUDE_LAYER_NAMES = frozenset([
    f"{PLUGIN_NAME} Annotations",
    f"{PLUGIN_NAME} Regions",
])


class RasterCapture:
    """Captures the visible canvas as a GeoTIFF file."""

    # Keep at most this many capture directories before cleaning old ones
    MAX_CAPTURE_DIRS = 5

    def __init__(self, iface, output_dir: Optional[str] = None):
        self.iface = iface
        self.output_dir = output_dir or tempfile.mkdtemp(prefix="hitl_capture_")
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        self._capture_count = 0
        self._old_dirs: list = []

    def capture_current_extent(self) -> Optional[str]:
        """Capture the current map canvas as a GeoTIFF.

        Renders all visible layers (except plugin overlay layers) into a
        composited RGB GeoTIFF at the current canvas resolution.

        Returns:
            Path to the output GeoTIFF, or None on failure.
        """
        canvas = self.iface.mapCanvas()
        extent = canvas.extent()
        width = canvas.width()
        height = canvas.height()

        if width <= 0 or height <= 0:
            self.iface.messageBar().pushMessage(
                PLUGIN_NAME, "Canvas has no size", level=2, duration=5,
            )
            return None

        # Set up map settings from current canvas, filtering out plugin layers
        settings = QgsMapSettings(canvas.mapSettings())
        settings.setOutputSize(QSize(width, height))
        settings.setExtent(extent)

        project = QgsProject.instance()
        visible_layers = [
            layer for layer in project.mapLayers().values()
            if layer.name() not in _EXCLUDE_LAYER_NAMES
        ]
        settings.setLayers(visible_layers)

        if not visible_layers:
            self.iface.messageBar().pushMessage(
                PLUGIN_NAME, "No visible layers to capture", level=2, duration=5,
            )
            return None

        # Render to QImage
        image = QImage(QSize(width, height), QImage.Format_ARGB32_Premultiplied)
        image.fill(0)

        painter = QPainter(image)
        job = QgsMapRendererCustomPainterJob(settings, painter)
        job.start()
        job.waitForFinished()
        painter.end()

        if image.isNull():
            self.iface.messageBar().pushMessage(
                PLUGIN_NAME, "Canvas render failed", level=2, duration=5,
            )
            return None

        # Output path
        self._capture_count += 1
        output_path = os.path.join(
            self.output_dir, f"capture_{self._capture_count:04d}.tif"
        )

        # Write GeoTIFF with GDAL
        if not self._write_geotiff(image, extent, settings.destinationCrs(), output_path):
            self.iface.messageBar().pushMessage(
                PLUGIN_NAME, "GeoTIFF write failed", level=2, duration=5,
            )
            return None

        return output_path

    @staticmethod
    def _write_geotiff(image: QImage, extent, crs, output_path: str) -> bool:
        """Write a QImage as a georeferenced 3-band RGB GeoTIFF.

        Uses GDAL WriteRaster with raw bytes to avoid gdal_array, which
        requires a numpy version matching the system GDAL build.
        """
        w, h = image.width(), image.height()

        # Convert QImage to raw BGRA bytes
        image = image.convertToFormat(QImage.Format_ARGB32)
        ptr = image.bits()
        ptr.setsize(w * h * 4)
        raw = bytes(ptr)

        # Extract R, G, B channels from BGRA layout (little-endian)
        r_bytes = bytearray(w * h)
        g_bytes = bytearray(w * h)
        b_bytes = bytearray(w * h)
        for i in range(w * h):
            off = i * 4
            b_bytes[i] = raw[off]
            g_bytes[i] = raw[off + 1]
            r_bytes[i] = raw[off + 2]

        # Affine transform: top-left origin, pixel size
        x_res = extent.width() / w
        y_res = extent.height() / h
        geo_transform = [
            extent.xMinimum(), x_res, 0.0,
            extent.yMaximum(), 0.0, -y_res,
        ]

        driver = gdal.GetDriverByName("GTiff")
        if driver is None:
            logger.error("GDAL GTiff driver not available")
            return False

        ds = driver.Create(output_path, w, h, 3, gdal.GDT_Byte)
        if ds is None:
            logger.error("Failed to create GeoTIFF: %s", output_path)
            return False

        ds.SetGeoTransform(geo_transform)

        srs = osr.SpatialReference()
        srs.ImportFromWkt(crs.toWkt())
        ds.SetProjection(srs.ExportToWkt())

        ds.GetRasterBand(1).WriteRaster(0, 0, w, h, bytes(r_bytes))
        ds.GetRasterBand(2).WriteRaster(0, 0, w, h, bytes(g_bytes))
        ds.GetRasterBand(3).WriteRaster(0, 0, w, h, bytes(b_bytes))
        ds.FlushCache()
        ds = None  # close

        return True

    def reset(self) -> None:
        """Start a new capture directory, retiring the old one.

        Old directories beyond MAX_CAPTURE_DIRS are deleted.
        """
        self._old_dirs.append(self.output_dir)
        self.output_dir = tempfile.mkdtemp(prefix="hitl_capture_")
        self._capture_count = 0

        # Prune oldest directories
        while len(self._old_dirs) > self.MAX_CAPTURE_DIRS:
            old = self._old_dirs.pop(0)
            shutil.rmtree(old, ignore_errors=True)

    def cleanup(self) -> None:
        """Delete all capture directories (call on plugin unload)."""
        for d in self._old_dirs:
            shutil.rmtree(d, ignore_errors=True)
        self._old_dirs.clear()
        if os.path.isdir(self.output_dir):
            shutil.rmtree(self.output_dir, ignore_errors=True)
