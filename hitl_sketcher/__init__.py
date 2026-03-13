"""HITL Sketcher - QGIS Plugin for interactive segmentation labeling."""

PLUGIN_NAME = "EasySegment"


def classFactory(iface):
    """QGIS plugin entry point."""
    from .plugin import HITLSketcherPlugin
    return HITLSketcherPlugin(iface)
