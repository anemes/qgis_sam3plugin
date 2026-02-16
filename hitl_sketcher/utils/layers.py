"""Helper functions for creating and finding QGIS layers."""

from __future__ import annotations

from typing import Optional

from qgis.core import QgsProject, QgsRasterLayer, QgsVectorLayer


def find_layer_by_name(name: str) -> Optional[QgsVectorLayer]:
    """Find a vector layer by name in the current project."""
    for layer in QgsProject.instance().mapLayers().values():
        if layer.name() == name and isinstance(layer, QgsVectorLayer):
            return layer
    return None


def find_raster_by_name(name: str) -> Optional[QgsRasterLayer]:
    """Find a raster layer by name in the current project."""
    for layer in QgsProject.instance().mapLayers().values():
        if layer.name() == name and isinstance(layer, QgsRasterLayer):
            return layer
    return None
