"""Shared helpers for labeling tools."""

from __future__ import annotations


def points_to_geojson(points) -> dict:
    """Convert a list of QgsPointXY to a closed GeoJSON Polygon geometry."""
    coords = [[p.x(), p.y()] for p in points] + [[points[0].x(), points[0].y()]]
    return {"type": "Polygon", "coordinates": [coords]}
