"""Prediction viewer: load prediction raster + confidence heatmap as styled layers."""

from __future__ import annotations

from typing import Optional

from qgis.core import (
    QgsProject,
    QgsRasterLayer,
    QgsRasterShader,
    QgsColorRampShader,
    QgsSingleBandPseudoColorRenderer,
)
from qgis.PyQt.QtGui import QColor


class PredictionViewer:
    """Loads prediction results into QGIS as styled raster layers."""

    def __init__(self, iface):
        self.iface = iface
        self._class_layer: Optional[QgsRasterLayer] = None
        self._confidence_layer: Optional[QgsRasterLayer] = None

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
        self._class_layer = QgsRasterLayer(class_raster_path, "HITL Prediction")
        if self._class_layer.isValid():
            self._style_class_layer(self._class_layer)
            project.addMapLayer(self._class_layer)

        # Load confidence heatmap
        if confidence_raster_path:
            self._confidence_layer = QgsRasterLayer(
                confidence_raster_path, "HITL Confidence"
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
            if layer.name() in ("HITL Prediction", "HITL Confidence"):
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
