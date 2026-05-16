"""Shared helpers for labeling tools."""

from __future__ import annotations

from typing import List


def points_to_geojson(points) -> dict:
    """Convert a list of QgsPointXY to a closed GeoJSON Polygon geometry."""
    coords = [[p.x(), p.y()] for p in points] + [[points[0].x(), points[0].y()]]
    return {"type": "Polygon", "coordinates": [coords]}


def densify_ring(ring: List[List[float]], max_segment: float = 500) -> List[List[float]]:
    """Insert intermediate points along edges longer than *max_segment* (CRS units).

    For EPSG:3857, units are metres — 500 m segments produce smooth curves
    when the ring is subsequently reprojected to EPSG:4326.
    """
    dense: List[List[float]] = []
    for i in range(len(ring) - 1):
        x0, y0 = ring[i]
        x1, y1 = ring[i + 1]
        dist = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        n_segments = max(1, int(dist / max_segment))
        for j in range(n_segments):
            t = j / n_segments
            dense.append([x0 + t * (x1 - x0), y0 + t * (y1 - y0)])
    dense.append(ring[-1])  # closing vertex
    return dense
