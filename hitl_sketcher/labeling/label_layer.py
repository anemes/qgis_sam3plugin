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

    # --- Layer URI templates ---

    _REGION_URI = (
        "Polygon?crs=EPSG:4326"
        "&field=region_id:integer"
        "&field=annotation_count:integer"
        "&field=created_at:string"
    )
    _ANNOTATION_URI = (
        "Polygon?crs=EPSG:4326"
        "&field=annotation_index:integer"
        "&field=class_id:integer"
        "&field=class_name:string"
        "&field=region_id:integer"
        "&field=source:string"
        "&field=iteration:integer"
    )

    def _replace_layer(self, uri: str, name: str, features: list,
                       style_fn) -> QgsVectorLayer:
        """Create a fresh memory layer pre-populated with *features*.

        Instead of editing a live layer (delete-all → add-all) while it is
        part of the QGraphicsScene, we build a brand-new layer off-scene,
        add it to the project, style it, and only then remove the old one.
        This avoids the stale-QGraphicsItem race that causes SIGSEGV in
        QGraphicsView::paintEvent().

        Returns the new layer.
        """
        project = QgsProject.instance()

        # Build the new layer entirely off-scene
        new_layer = QgsVectorLayer(uri, name, "memory")
        if features:
            new_layer.startEditing()
            new_layer.addFeatures(features)
            new_layer.commitChanges()
        new_layer.updateExtents()

        # Add new layer to project (builds fresh scene items)
        project.addMapLayer(new_layer)
        style_fn(new_layer)

        return new_layer

    def sync_regions(self) -> list[dict]:
        """Fetch regions from backend and rebuild the QGIS layer.

        Returns list of region dicts with annotation counts.
        """
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

        # Build features using a temporary layer's fields
        tmp = QgsVectorLayer(self._REGION_URI, "tmp", "memory")
        fields = tmp.fields()

        features = []
        result = []
        for r in regions:
            rid = r["region_id"]
            geom = self._geojson_to_geometry(r["geometry"])
            if geom is None or geom.isEmpty():
                logger.warning("Skipping region %d: invalid geometry", rid)
                continue

            feat = QgsFeature(fields)
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

        # Remove old layer, create fresh one
        old = self._region_layer
        self._region_layer = self._replace_layer(
            self._REGION_URI, self.REGIONS_LAYER_NAME, features,
            self._style_region_layer,
        )
        self._remove_old_layer(old)
        logger.info("Synced %d regions to layer", len(features))

        return result

    def sync_annotations(
        self,
        class_colors: Optional[dict] = None,
        class_names: Optional[dict] = None,
    ) -> int:
        """Fetch annotations from backend and rebuild the QGIS layer.

        class_colors / class_names: caller-supplied dicts {class_id: value}
        that take priority over whatever the backend returns.  Pass the local
        ClassManager data so annotations are styled correctly even before the
        user has clicked "Sync Classes".

        Returns count of annotations synced.
        """
        try:
            annotations = self.client.get_annotations(crs="EPSG:4326")
            classes = self.client.get_classes()
        except Exception as e:
            logger.warning("Failed to sync annotations: %s", e)
            return 0

        # Build class lookup — local defs take precedence over backend so that
        # newly-added classes are styled immediately without an explicit sync.
        self._class_colors = {c["class_id"]: c["color"] for c in classes}
        self._class_names = {c["class_id"]: c["name"] for c in classes}
        if class_colors:
            self._class_colors.update(class_colors)
        if class_names:
            self._class_names.update(class_names)
        local_names = self._class_names

        # Build features using a temporary layer's fields
        tmp = QgsVectorLayer(self._ANNOTATION_URI, "tmp", "memory")
        fields = tmp.fields()

        features = []
        for idx, ann in enumerate(annotations):
            geom = self._geojson_to_geometry(ann["geometry"])
            if geom is None or geom.isEmpty():
                logger.warning("Skipping annotation %d: invalid geometry", idx)
                continue

            feat = QgsFeature(fields)
            feat.setGeometry(geom)
            feat.setAttribute("annotation_index", idx)
            feat.setAttribute("class_id", ann.get("class_id", 0))
            feat.setAttribute("class_name", local_names.get(ann.get("class_id", 0), "?"))
            feat.setAttribute("region_id", ann.get("region_id", 0))
            feat.setAttribute("source", ann.get("source", ""))
            feat.setAttribute("iteration", ann.get("iteration", 0))
            features.append(feat)

        # Remove old layer, create fresh one
        old = self._annotation_layer
        self._annotation_layer = self._replace_layer(
            self._ANNOTATION_URI, self.ANNOTATIONS_LAYER_NAME, features,
            self._style_annotation_layer,
        )
        self._remove_old_layer(old)
        logger.info(
            "Synced %d annotations to layer (class_colors: %s)",
            len(features), list(self._class_colors.keys()),
        )

        return len(features)

    def sync_all(
        self,
        class_colors: Optional[dict] = None,
        class_names: Optional[dict] = None,
    ) -> None:
        """Sync both layers from the backend."""
        self.sync_regions()
        self.sync_annotations(class_colors=class_colors, class_names=class_names)

    def _remove_old_layer(self, layer: Optional[QgsVectorLayer]) -> None:
        """Safely remove an old layer from the project."""
        if layer is None:
            return
        try:
            project = QgsProject.instance()
            if layer.id() in project.mapLayers():
                project.removeMapLayer(layer.id())
        except RuntimeError:
            pass  # C++ object already deleted

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

    def _style_region_layer(self, layer: Optional[QgsVectorLayer] = None) -> None:
        """Style regions as orange dashed outlines with transparent fill."""
        layer = layer or self._region_layer
        if layer is None:
            return
        symbol = QgsFillSymbol.createSimple({
            "color": "255,165,0,50",
            "outline_color": "255,165,0,220",
            "outline_style": "dash",
            "outline_width": "2.0",
        })
        layer.renderer().setSymbol(symbol)
        layer.triggerRepaint()

    def _style_annotation_layer(self, layer: Optional[QgsVectorLayer] = None) -> None:
        """Style annotations using rule-based renderer with class colors."""
        layer = layer or self._annotation_layer
        if layer is None:
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
        layer.setRenderer(renderer)

    @staticmethod
    def _geojson_to_geometry(geom_dict: dict) -> Optional[QgsGeometry]:
        """Convert a GeoJSON geometry dict to QgsGeometry safely.

        Builds QgsGeometry directly from coordinate arrays, bypassing the
        OGR → WKT → QgsGeometry pipeline entirely.  SAM-derived polygons
        carry 15-digit coordinate precision after reprojection; OGR's
        ExportToWkt() uses %.15g formatting which produces WKT strings that
        overflow QGIS's internal WKT parser (std::vector larger than
        max_size()).  Constructing from QgsPointXY arrays avoids WKT
        altogether.

        Coordinates are rounded to 6 decimal places (~10 cm in EPSG:4326).
        """
        from qgis.core import QgsPointXY

        def _pt(coord, prec=6):
            return QgsPointXY(round(coord[0], prec), round(coord[1], prec))

        try:
            geom_type = geom_dict.get("type", "")
            coords = geom_dict.get("coordinates", [])

            if geom_type == "Polygon":
                rings = [[_pt(c) for c in ring] for ring in coords]
                return QgsGeometry.fromPolygonXY(rings)

            if geom_type == "MultiPolygon":
                polygons = [
                    [[_pt(c) for c in ring] for ring in polygon]
                    for polygon in coords
                ]
                return QgsGeometry.fromMultiPolygonXY(polygons)

            # Fallback for other types (Point, LineString, etc.) — use OGR/WKT
            # with rounded coordinates as before.
            from osgeo import ogr

            def _round_coords(c, prec=6):
                if not c:
                    return c
                if isinstance(c[0], (int, float)):
                    return [round(v, prec) for v in c]
                return [_round_coords(sub, prec) for sub in c]

            if "coordinates" in geom_dict:
                geom_dict = dict(geom_dict)
                geom_dict["coordinates"] = _round_coords(geom_dict["coordinates"])

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
