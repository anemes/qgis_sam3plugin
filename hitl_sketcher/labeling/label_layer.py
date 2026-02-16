"""QGIS layer management for HITL regions and annotations.

Creates in-memory vector layers that are synced from the backend.
The backend GeoPackage is the single source of truth — these layers
are read-only visualisations that refresh after each mutation.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from qgis.core import (
    QgsFeature,
    QgsGeometry,
    QgsProject,
    QgsVectorLayer,
    QgsRuleBasedRenderer,
    QgsFillSymbol,
)
from qgis.PyQt.QtGui import QColor

logger = logging.getLogger(__name__)


class LabelLayerManager:
    """Manages in-memory QGIS layers synced from the backend."""

    ANNOTATIONS_LAYER_NAME = "HITL Annotations"
    REGIONS_LAYER_NAME = "HITL Regions"

    def __init__(self, iface, client):
        self.iface = iface
        self.client = client
        self._annotation_layer: Optional[QgsVectorLayer] = None
        self._region_layer: Optional[QgsVectorLayer] = None
        self._class_colors: dict[int, str] = {}  # class_id -> hex color
        self._class_names: dict[int, str] = {}  # class_id -> name

    @property
    def annotation_layer(self) -> Optional[QgsVectorLayer]:
        return self._annotation_layer

    @property
    def region_layer(self) -> Optional[QgsVectorLayer]:
        return self._region_layer

    def ensure_layers(self) -> None:
        """Create the in-memory layers if they don't exist yet."""
        project = QgsProject.instance()

        # Check if layers already exist
        self._region_layer = None
        self._annotation_layer = None
        for layer in project.mapLayers().values():
            if layer.name() == self.REGIONS_LAYER_NAME:
                self._region_layer = layer
            elif layer.name() == self.ANNOTATIONS_LAYER_NAME:
                self._annotation_layer = layer

        if self._region_layer is None:
            self._region_layer = QgsVectorLayer(
                "Polygon?crs=EPSG:4326"
                "&field=region_id:integer"
                "&field=annotation_count:integer"
                "&field=created_at:string",
                self.REGIONS_LAYER_NAME,
                "memory",
            )
            project.addMapLayer(self._region_layer)

        # Always apply region styling
        self._style_region_layer()

        if self._annotation_layer is None:
            self._annotation_layer = QgsVectorLayer(
                "Polygon?crs=EPSG:4326"
                "&field=annotation_index:integer"
                "&field=class_id:integer"
                "&field=class_name:string"
                "&field=region_id:integer"
                "&field=source:string"
                "&field=iteration:integer",
                self.ANNOTATIONS_LAYER_NAME,
                "memory",
            )
            project.addMapLayer(self._annotation_layer)

    def sync_regions(self) -> list[dict]:
        """Fetch regions from backend and rebuild the QGIS layer.

        Returns list of region dicts with annotation counts.
        """
        self.ensure_layers()
        if self._region_layer is None:
            return []

        try:
            regions = self.client.get_regions(crs="EPSG:4326")
            annotations = self.client.get_annotations(crs="EPSG:4326")
        except Exception as e:
            logger.warning("Failed to sync regions: %s", e)
            return []

        # Count annotations per region
        counts: dict[int, int] = {}
        for ann in annotations:
            rid = ann.get("region_id", 0)
            counts[rid] = counts.get(rid, 0) + 1

        # Rebuild layer — use data provider directly (no edit session)
        dp = self._region_layer.dataProvider()
        dp.deleteFeatures(dp.allFeatureIds())

        features = []
        result = []
        for r in regions:
            rid = r["region_id"]
            geom = self._geojson_to_geometry(r["geometry"])
            if geom is None or geom.isEmpty():
                logger.warning("Skipping region %d: invalid geometry", rid)
                continue

            feat = QgsFeature(self._region_layer.fields())
            feat.setGeometry(geom)
            feat.setAttribute("region_id", rid)
            feat.setAttribute("annotation_count", counts.get(rid, 0))
            feat.setAttribute("created_at", r.get("created_at", ""))
            features.append(feat)
            result.append({
                "region_id": rid,
                "annotation_count": counts.get(rid, 0),
                "created_at": r.get("created_at", ""),
            })

        if features:
            dp.addFeatures(features)
        self._region_layer.updateExtents()
        self._region_layer.triggerRepaint()
        logger.info("Synced %d regions to layer", len(features))

        return result

    def sync_annotations(self) -> int:
        """Fetch annotations from backend and rebuild the QGIS layer.

        Returns count of annotations synced.
        """
        self.ensure_layers()
        if self._annotation_layer is None:
            return 0

        try:
            annotations = self.client.get_annotations(crs="EPSG:4326")
            classes = self.client.get_classes()
        except Exception as e:
            logger.warning("Failed to sync annotations: %s", e)
            return 0

        # Build class lookup
        class_names = {c["class_id"]: c["name"] for c in classes}
        self._class_colors = {c["class_id"]: c["color"] for c in classes}
        self._class_names = class_names

        # Rebuild layer — use data provider directly (no edit session)
        dp = self._annotation_layer.dataProvider()
        dp.deleteFeatures(dp.allFeatureIds())

        features = []
        for idx, ann in enumerate(annotations):
            geom = self._geojson_to_geometry(ann["geometry"])
            if geom is None or geom.isEmpty():
                logger.warning("Skipping annotation %d: invalid geometry", idx)
                continue

            feat = QgsFeature(self._annotation_layer.fields())
            feat.setGeometry(geom)
            feat.setAttribute("annotation_index", idx)
            feat.setAttribute("class_id", ann.get("class_id", 0))
            feat.setAttribute("class_name", class_names.get(ann.get("class_id", 0), "?"))
            feat.setAttribute("region_id", ann.get("region_id", 0))
            feat.setAttribute("source", ann.get("source", ""))
            feat.setAttribute("iteration", ann.get("iteration", 0))
            features.append(feat)

        if features:
            dp.addFeatures(features)
        self._annotation_layer.updateExtents()

        # Update annotation styling based on class colors
        self._style_annotation_layer()
        self._annotation_layer.triggerRepaint()
        logger.info(
            "Synced %d annotations to layer (class_colors: %s)",
            len(features), list(self._class_colors.keys()),
        )

        return len(features)

    def sync_all(self) -> None:
        """Sync both layers from the backend."""
        self.sync_regions()
        self.sync_annotations()

    def remove_layers(self) -> None:
        """Remove layers from the project (safe during QGIS shutdown)."""
        try:
            project = QgsProject.instance()
            if self._region_layer:
                project.removeMapLayer(self._region_layer.id())
            if self._annotation_layer:
                project.removeMapLayer(self._annotation_layer.id())
        except RuntimeError:
            pass  # C++ objects already deleted during QGIS shutdown
        self._region_layer = None
        self._annotation_layer = None

    def _style_region_layer(self) -> None:
        """Style regions as orange dashed outlines with transparent fill."""
        if self._region_layer is None:
            return
        symbol = QgsFillSymbol.createSimple({
            "color": "255,165,0,50",
            "outline_color": "255,165,0,220",
            "outline_style": "dash",
            "outline_width": "2.0",
        })
        self._region_layer.renderer().setSymbol(symbol)
        self._region_layer.triggerRepaint()

    def _style_annotation_layer(self) -> None:
        """Style annotations using rule-based renderer with class colors."""
        if self._annotation_layer is None:
            return

        # Always set up renderer, even without class colors (use fallback)
        root_rule = QgsRuleBasedRenderer.Rule(None)

        for class_id, hex_color in self._class_colors.items():
            color = QColor(hex_color)
            fill_color = QColor(color)
            fill_color.setAlpha(80)

            symbol = QgsFillSymbol.createSimple({
                "color": f"{fill_color.red()},{fill_color.green()},{fill_color.blue()},{fill_color.alpha()}",
                "outline_color": f"{color.red()},{color.green()},{color.blue()},220",
                "outline_width": "0.8",
            })

            rule = QgsRuleBasedRenderer.Rule(symbol)
            rule.setFilterExpression(f'"class_id" = {class_id}')
            rule.setLabel(self._class_names.get(class_id, f"Class {class_id}"))
            root_rule.appendChild(rule)

        # Fallback for unknown classes — bright magenta so it's always visible
        fallback_sym = QgsFillSymbol.createSimple({
            "color": "255,0,255,80",
            "outline_color": "255,0,255,220",
            "outline_width": "1.0",
        })
        fallback_rule = QgsRuleBasedRenderer.Rule(fallback_sym)
        fallback_rule.setIsElse(True)
        fallback_rule.setLabel("Other")
        root_rule.appendChild(fallback_rule)

        renderer = QgsRuleBasedRenderer(root_rule)
        self._annotation_layer.setRenderer(renderer)

    @staticmethod
    def _geojson_to_geometry(geom_dict: dict) -> Optional[QgsGeometry]:
        """Convert a GeoJSON geometry dict to QgsGeometry safely.

        Uses OGR (always available in QGIS) for robust GeoJSON parsing,
        converting through WKT which QgsGeometry handles reliably.
        """
        try:
            from osgeo import ogr
            geojson_str = json.dumps(geom_dict)
            ogr_geom = ogr.CreateGeometryFromJson(geojson_str)
            if ogr_geom is None:
                logger.warning("OGR failed to parse GeoJSON: %s", geojson_str[:200])
                return None
            wkt = ogr_geom.ExportToWkt()
            geom = QgsGeometry.fromWkt(wkt)
            if geom is None or geom.isEmpty():
                logger.warning("QgsGeometry.fromWkt failed for: %s", wkt[:200])
                return None
            return geom
        except Exception as e:
            logger.warning("Failed to parse geometry: %s", e)
            return None
