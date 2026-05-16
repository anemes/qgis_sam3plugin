"""Prediction viewer: load vectorized predictions as styled QGIS layers."""

from __future__ import annotations

from typing import Optional

from osgeo import ogr

from .. import PLUGIN_NAME
from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsFillSymbol,
    QgsLayerTreeGroup,
    QgsProject,
    QgsRendererCategory,
    QgsVectorLayer,
)
from qgis.PyQt.QtGui import QColor


class PredictionViewer:
    """Loads prediction results into QGIS as styled vector layers."""

    def __init__(self, iface):
        self.iface = iface
        self._vector_layers: dict[str, str] = {}  # job_id -> QGIS layer_id

    # Class color palette (shared with raster styling)
    _CLASS_COLORS = [
        QColor(0, 0, 0, 0),       # 0: ignore (transparent)
        QColor(128, 128, 128),     # 1: background
        QColor(255, 0, 0),         # 2
        QColor(0, 255, 0),         # 3
        QColor(0, 0, 255),         # 4
        QColor(255, 255, 0),       # 5
        QColor(255, 0, 255),       # 6
        QColor(0, 255, 255),       # 7
        QColor(255, 128, 0),       # 8
        QColor(128, 0, 255),       # 9
    ]

    @staticmethod
    def _get_or_create_group(group_name: str) -> QgsLayerTreeGroup:
        """Return an existing layer tree group or create one at the top."""
        root = QgsProject.instance().layerTreeRoot()
        group = root.findGroup(group_name)
        if group is None:
            group = root.insertGroup(0, group_name)
        return group

    def load_vector_prediction(
        self,
        gpkg_path: str,
        job_id: str,
        display_name: Optional[str] = None,
        group_name: Optional[str] = None,
    ) -> Optional[QgsVectorLayer]:
        """Load vectorized predictions from GeoPackage as a categorized layer.

        Args:
            gpkg_path:    Path to the GeoPackage file with prediction polygons.
            job_id:       Unique job identifier used as a stable key.
            display_name: Human-readable layer name shown in the layer panel.
                          Defaults to ``"Inference: {job_id}"``.
            group_name:   Layer tree group to place the layer in.  When given,
                          the group is created at the top of the tree if it
                          doesn't exist yet.  When ``None`` the layer is added
                          to the root (previous behaviour).
        """
        ds = ogr.Open(gpkg_path)
        if ds is None or ds.GetLayerCount() == 0:
            return None
        layer_name = ds.GetLayer(0).GetName()
        ds = None  # close

        uri = f"{gpkg_path}|layername={layer_name}"
        name = display_name or f"Inference: {job_id}"
        layer = QgsVectorLayer(uri, name, "ogr")
        if not layer.isValid():
            return None

        self._style_vector_layer(layer)

        if group_name:
            group = self._get_or_create_group(group_name)
            QgsProject.instance().addMapLayer(layer, False)
            group.addLayer(layer)
        else:
            QgsProject.instance().addMapLayer(layer)

        self._vector_layers[job_id] = layer.id()
        return layer

    def remove_vector_prediction(self, job_id: str) -> None:
        """Remove a vector prediction layer by job_id."""
        layer_id = self._vector_layers.pop(job_id, None)
        if layer_id:
            try:
                QgsProject.instance().removeMapLayer(layer_id)
            except RuntimeError:
                pass
            self.iface.mapCanvas().refresh()

    def _style_vector_layer(self, layer: QgsVectorLayer) -> None:
        """Apply categorized styling by class_id field."""
        field_idx = layer.fields().indexOf("class_id")
        if field_idx < 0:
            return

        has_class_name = layer.fields().indexOf("class_name") >= 0
        unique_values = sorted(layer.uniqueValues(field_idx))

        name_lookup: dict[int, str] = {}
        if has_class_name:
            for feat in layer.getFeatures():
                cid = int(feat["class_id"])
                if cid not in name_lookup:
                    name_lookup[cid] = str(feat["class_name"])

        categories = []
        for val in unique_values:
            val_int = int(val)
            if val_int < len(self._CLASS_COLORS):
                color = self._CLASS_COLORS[val_int]
            else:
                color = QColor(200, 200, 200)

            fill_color = QColor(color)
            fill_color.setAlpha(100)

            symbol = QgsFillSymbol.createSimple({
                "color": f"{fill_color.red()},{fill_color.green()},{fill_color.blue()},{fill_color.alpha()}",
                "outline_color": f"{color.red()},{color.green()},{color.blue()},220",
                "outline_width": "0.4",
            })

            label = name_lookup.get(val_int, f"Class {val_int}")
            categories.append(QgsRendererCategory(val_int, symbol, label))

        renderer = QgsCategorizedSymbolRenderer("class_id", categories)
        layer.setRenderer(renderer)
