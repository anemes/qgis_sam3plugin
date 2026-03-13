"""Correction mode tool: draw correction regions over predictions to relabel.

Phase 4 implementation placeholder. When activated:
1. User draws a correction region polygon
2. Existing annotations inside that region are deleted
3. New annotation region is created
4. User re-labels inside the correction region
"""

from __future__ import annotations

from .. import PLUGIN_NAME
from qgis.gui import QgsMapTool
from qgis.PyQt.QtCore import Qt


class CorrectionTool(QgsMapTool):
    """Map tool for correction regions (Phase 4 placeholder)."""

    def __init__(self, canvas, client):
        super().__init__(canvas)
        self.client = client

    def activate(self) -> None:
        super().activate()
        self.canvas().setCursor(Qt.CrossCursor)
        from qgis.utils import iface
        iface.messageBar().pushMessage(
            PLUGIN_NAME,
            "Correction mode: not yet implemented (Phase 4)",
            level=1,
            duration=5,
        )
