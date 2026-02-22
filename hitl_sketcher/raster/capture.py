"""Export visible map extent as GeoTIFF for labeling.

Uses QgsRasterPipe + QgsRasterFileWriter to render the active raster
layer (XYZ, WMS, or file-based) to a georeferenced GeoTIFF at the
current view resolution.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from qgis.core import (
    QgsProject,
    QgsRasterFileWriter,
    QgsRasterLayer,
    QgsRasterPipe,
    QgsRectangle,
)


class RasterCapture:
    """Captures the visible raster extent as a GeoTIFF file."""

    # Keep at most this many capture directories before cleaning old ones
    MAX_CAPTURE_DIRS = 5

    def __init__(self, iface, output_dir: Optional[str] = None):
        self.iface = iface
        self.output_dir = output_dir or tempfile.mkdtemp(prefix="hitl_capture_")
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        self._capture_count = 0
        self._old_dirs: list = []

    def capture_current_extent(
        self,
        raster_layer: Optional[QgsRasterLayer] = None,
        resolution: Optional[float] = None,
    ) -> Optional[str]:
        """Capture the current map extent as a GeoTIFF.

        Args:
            raster_layer: Specific raster layer to capture. If None, uses
                         the first visible raster layer.
            resolution: Output resolution in CRS units. If None, uses the
                       current canvas resolution.

        Returns:
            Path to the output GeoTIFF, or None on failure.
        """
        canvas = self.iface.mapCanvas()
        extent = canvas.extent()

        # Find raster layer
        if raster_layer is None:
            raster_layer = self._find_visible_raster()
        if raster_layer is None:
            self.iface.messageBar().pushMessage(
                "HITL Sketcher",
                "No visible raster layer found",
                level=2,
                duration=5,
            )
            return None

        # Compute output size
        if resolution is None:
            # Use canvas pixel resolution
            width = canvas.width()
            height = canvas.height()
        else:
            width = int(extent.width() / resolution)
            height = int(extent.height() / resolution)

        # Output path
        self._capture_count += 1
        output_path = os.path.join(
            self.output_dir, f"capture_{self._capture_count:04d}.tif"
        )

        # Create pipe
        pipe = QgsRasterPipe()
        provider = raster_layer.dataProvider()
        if not pipe.set(provider.clone()):
            self.iface.messageBar().pushMessage(
                "HITL Sketcher",
                "Failed to create raster pipe",
                level=2,
                duration=5,
            )
            return None

        # Write
        writer = QgsRasterFileWriter(output_path)
        writer.setOutputFormat("GTiff")

        crs = canvas.mapSettings().destinationCrs()
        error = writer.writeRaster(
            pipe,
            width,
            height,
            extent,
            crs,
        )

        if error != QgsRasterFileWriter.NoError:
            self.iface.messageBar().pushMessage(
                "HITL Sketcher",
                f"Raster export failed (error {error})",
                level=2,
                duration=5,
            )
            return None

        return output_path

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

    def _find_visible_raster(self) -> Optional[QgsRasterLayer]:
        """Find the first visible raster layer in the project."""
        project = QgsProject.instance()
        root = project.layerTreeRoot()

        for tree_layer in root.findLayers():
            if tree_layer.isVisible():
                layer = tree_layer.layer()
                if isinstance(layer, QgsRasterLayer):
                    return layer

        return None
