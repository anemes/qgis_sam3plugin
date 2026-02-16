"""HITL Sketcher - QGIS Plugin for interactive segmentation labeling."""


def classFactory(iface):
    """QGIS plugin entry point."""
    from .plugin import HITLSketcherPlugin
    return HITLSketcherPlugin(iface)
