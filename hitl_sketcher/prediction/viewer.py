"""Prediction viewer: load prediction raster + confidence heatmap as styled layers."""

from __future__ import annotations

from typing import Optional

from .. import PLUGIN_NAME
from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsFillSymbol,
    QgsProject,
    QgsRasterLayer,
    QgsRasterShader,
    QgsColorRampShader,
    QgsRendererCategory,
    QgsSingleBandPseudoColorRenderer,
    QgsVectorLayer,
)
from qgis.PyQt.QtGui import QColor


class PredictionViewer:
    """Loads prediction results into QGIS as styled raster/vector layers."""

    def __init__(self, iface):
        self.iface = iface
        self._class_layer: Optional[QgsRasterLayer] = None
        self._confidence_layer: Optional[QgsRasterLayer] = None
        self._vector_layers: dict[str, str] = {}  # job_id -> QGIS layer_id

    def load_prediction(
        self,
        class_raster_path: str,
        confidence_raster_path: Optional[str] = None,
    ) -> None:
        """Load prediction rasters into QGIS with styling."""
        project = QgsProject.instance()

        # Remove previous prediction layers
        self._remove_old_layers()

        # Load class prediction
        self._class_layer = QgsRasterLayer(class_raster_path, f"{PLUGIN_NAME} Prediction")
        if self._class_layer.isValid():
            self._style_class_layer(self._class_layer)
            project.addMapLayer(self._class_layer)

        # Load confidence heatmap
        if confidence_raster_path:
            self._confidence_layer = QgsRasterLayer(
                confidence_raster_path, f"{PLUGIN_NAME} Confidence"
            )
            if self._confidence_layer.isValid():
                self._style_confidence_layer(self._confidence_layer)
                project.addMapLayer(self._confidence_layer)
                # Make semi-transparent
                self._confidence_layer.renderer().setOpacity(0.5)

    def _remove_old_layers(self) -> None:
        """Remove previous prediction layers."""
        project = QgsProject.instance()
        to_remove = []
        for layer_id, layer in project.mapLayers().items():
            if layer.name() in (f"{PLUGIN_NAME} Prediction", f"{PLUGIN_NAME} Confidence"):
                to_remove.append(layer_id)
        for layer_id in to_remove:
            project.removeMapLayer(layer_id)

    def _style_class_layer(self, layer: QgsRasterLayer) -> None:
        """Apply categorical coloring to class prediction layer."""
        # Default color palette for classes
        colors = [
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

        shader = QgsRasterShader()
        color_ramp = QgsColorRampShader()
        color_ramp.setColorRampType(QgsColorRampShader.Exact)

        items = []
        for i, color in enumerate(colors):
            item = QgsColorRampShader.ColorRampItem(float(i), color, f"Class {i}")
            items.append(item)
        color_ramp.setColorRampItemList(items)
        shader.setRasterShaderFunction(color_ramp)

        renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
        layer.setRenderer(renderer)

    def _style_confidence_layer(self, layer: QgsRasterLayer) -> None:
        """Apply continuous gradient to confidence/entropy layer.

        Green (low entropy = confident) → Red (high entropy = uncertain)
        """
        shader = QgsRasterShader()
        color_ramp = QgsColorRampShader()
        color_ramp.setColorRampType(QgsColorRampShader.Interpolated)

        items = [
            QgsColorRampShader.ColorRampItem(0.0, QColor(0, 200, 0), "Confident"),
            QgsColorRampShader.ColorRampItem(0.5, QColor(255, 255, 0), "Moderate"),
            QgsColorRampShader.ColorRampItem(1.0, QColor(255, 0, 0), "Uncertain"),
        ]
        color_ramp.setColorRampItemList(items)
        shader.setRasterShaderFunction(color_ramp)

        renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
        layer.setRenderer(renderer)

    # --- Vector prediction layers ---

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

    def load_vector_prediction(
        self, gpkg_path: str, job_id: str
    ) -> Optional[QgsVectorLayer]:
        """Load vectorized predictions from GeoPackage as a categorized layer."""
        from osgeo import ogr

        ds = ogr.Open(gpkg_path)
        if ds is None or ds.GetLayerCount() == 0:
            return None
        layer_name = ds.GetLayer(0).GetName()
        ds = None  # close

        uri = f"{gpkg_path}|layername={layer_name}"
        display_name = f"Inference: {job_id}"
        layer = QgsVectorLayer(uri, display_name, "ogr")
        if not layer.isValid():
            return None

        self._style_vector_layer(layer)
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
