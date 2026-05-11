"""Main QGIS plugin class: toolbar, dock widgets, and action management."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QAction, QToolBar
from qgis.gui import QgisInterface

from . import PLUGIN_NAME
from .connection.panel import ConnectionPanel
from .labeling.label_layer import LabelLayerManager
from .labeling.polygon_tool import PolygonTool
from .labeling.project_panel import ProjectPanel
from .labeling.region_tool import RegionTool
from .labeling.sam_tool import SAMTool
from .prediction.inference_tool import AOIDrawTool
from .prediction.standalone_panel import StandaloneInferencePanel
from .prediction.viewer import PredictionViewer
from .raster.capture import RasterCapture


class HITLSketcherPlugin:
    """HITL Sketcher QGIS Plugin.

    Provides:
    - Connection panel: configure backend URL, check health
    - Project panel: project/class/region management, SAM3 controls
    - Region + annotation layers synced from backend
    - Manual polygon tool: draw polygons by hand
    - SAM3 tool: click/box interactive segmentation
    - Inference tool: trigger prediction on AOI, view results
    """

    def __init__(self, iface: QgisInterface):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.toolbar: Optional[QToolBar] = None
        self.actions: list[QAction] = []

        # Panels
        self.connection_panel: Optional[ConnectionPanel] = None
        self.project_panel: Optional[ProjectPanel] = None
        self.standalone_panel: Optional[StandaloneInferencePanel] = None

        # Tools
        self.region_tool: Optional[RegionTool] = None
        self.polygon_tool: Optional[PolygonTool] = None
        self.sam_tool: Optional[SAMTool] = None
        self.aoi_tool: Optional[AOIDrawTool] = None
        self.aoi_tool_standalone: Optional[AOIDrawTool] = None

        # Managers / shared services
        self.label_manager: Optional[LabelLayerManager] = None
        self.prediction_viewer: Optional[PredictionViewer] = None
        self.raster_capture: Optional[RasterCapture] = None

    def initGui(self) -> None:
        """Initialize the plugin GUI: toolbar, dock widgets, map tools."""
        self.toolbar = self.iface.addToolBar(PLUGIN_NAME)
        self.toolbar.setObjectName("HITLSketcherToolbar")

        icon_dir = Path(__file__).parent / "icons"

        # --- Connection panel ---
        self.connection_panel = ConnectionPanel(self.iface)
        self._add_dock_action(
            "Backend Connection",
            icon_dir / "settings.svg",
            self.connection_panel,
        )
        # Auto-sync on successful connection
        self.connection_panel.connected.connect(self._on_backend_connected)

        # --- Shared prediction viewer (used by labelling and standalone panels) ---
        self.prediction_viewer = PredictionViewer(self.iface)

        # --- Labelling panel (unified: project, classes, regions, SAM3, inference) ---
        self.project_panel = ProjectPanel(
            self.iface, self.connection_panel.client, self.prediction_viewer
        )
        self._add_dock_action(
            "Labelling",
            icon_dir / "settings.svg",
            self.project_panel,
        )

        # --- Label layer manager (in-memory layers synced from backend) ---
        self.label_manager = LabelLayerManager(
            self.iface, self.connection_panel.client
        )

        # Connect project panel signals — single sync point for all data mutations
        self.project_panel.layers_changed.connect(self._sync_all)

        # --- Region drawing tool (activated from "Add Region" button in panel) ---
        self.region_tool = RegionTool(self.canvas, self.connection_panel.client)
        self.region_tool.region_created.connect(self._sync_all)
        self.project_panel.add_region_requested.connect(self._activate_region_tool)

        # --- Manual polygon annotation tool ---
        self.polygon_tool = PolygonTool(
            self.canvas,
            self.connection_panel.client,
            get_class_id=self.project_panel.get_active_class_id,
            get_region_id=self.project_panel.get_active_region_id,
        )
        self.polygon_tool.annotation_saved.connect(self._sync_all)

        # --- SAM3 tool ---
        self.sam_tool = SAMTool(
            self.canvas, self.connection_panel.client, self.project_panel
        )

        # Tool activation from panel buttons (no separate toolbar buttons)
        self.project_panel.polygon_tool_requested.connect(self._activate_polygon_tool)
        self.project_panel.sam_tool_requested.connect(self._activate_sam_tool)

        # When project panel starts a SAM session, update the tool with image info
        self.project_panel.session_started.connect(self._on_sam_session_started)
        # When mask is accepted, sync layers
        self.project_panel.mask_accepted.connect(self._sync_all)

        # Detect when map tool changes externally (e.g., user clicks pan/zoom)
        self.canvas.mapToolSet.connect(self._on_map_tool_changed)

        # --- Shared raster capture (used by SAM panel and standalone inference) ---
        self.raster_capture = RasterCapture(self.iface)

        # --- AOI drawing tool for the Labelling panel's inference section ---
        self.aoi_tool = AOIDrawTool(self.canvas)
        self.project_panel.draw_aoi_requested.connect(self._activate_aoi_tool)
        self.aoi_tool.aoi_drawn.connect(self.project_panel.set_aoi)
        self.project_panel.inference_promoted.connect(self._sync_all)

        # --- Models panel (standalone inference, no training dependency) ---
        self.standalone_panel = StandaloneInferencePanel(
            self.iface,
            self.connection_panel.client,
            self.prediction_viewer,
            self.raster_capture,
        )
        self._add_dock_action(
            "Models",
            icon_dir / "settings.svg",
            self.standalone_panel,
        )

        self.aoi_tool_standalone = AOIDrawTool(self.canvas)
        self.standalone_panel.draw_aoi_requested.connect(
            self._activate_aoi_tool_standalone
        )
        self.aoi_tool_standalone.aoi_drawn.connect(self.standalone_panel.set_aoi)

    def unload(self) -> None:
        """Cleanup on plugin unload."""
        # Disconnect signals to prevent handler accumulation on reload
        try:
            self.canvas.mapToolSet.disconnect(self._on_map_tool_changed)
        except Exception:
            pass

        for action in self.actions:
            self.iface.removeToolBarIcon(action)

        if self.toolbar:
            del self.toolbar

        if self.connection_panel:
            self.iface.removeDockWidget(self.connection_panel)
        if self.project_panel:
            if hasattr(self.project_panel, '_raster_capture') and self.project_panel._raster_capture:
                self.project_panel._raster_capture.cleanup()
            if self.project_panel._poll_timer:
                self.project_panel._poll_timer.stop()
            self.iface.removeDockWidget(self.project_panel)
        if self.standalone_panel:
            if self.standalone_panel._poll_timer:
                self.standalone_panel._poll_timer.stop()
            self.iface.removeDockWidget(self.standalone_panel)

        # Destroy canvas items from all tools (safe: no more paint events on unload)
        for tool in (
            self.polygon_tool, self.region_tool, self.sam_tool,
            self.aoi_tool, self.aoi_tool_standalone,
        ):
            if tool and hasattr(tool, 'destroy'):
                try:
                    tool.destroy()
                except RuntimeError:
                    pass  # C++ object already deleted

        if self.label_manager:
            self.label_manager.remove_layers()

    def _add_dock_action(self, title: str, icon_path: Path, widget) -> None:
        """Add a dock widget with a toolbar toggle action."""
        self.iface.addDockWidget(Qt.RightDockWidgetArea, widget)
        widget.setVisible(False)

        action = QAction(title, self.iface.mainWindow())
        action.setCheckable(True)
        action.triggered.connect(lambda checked: widget.setVisible(checked))
        widget.visibilityChanged.connect(action.setChecked)
        self.toolbar.addAction(action)
        self.actions.append(action)

    def _on_backend_connected(self) -> None:
        """Auto-sync everything when backend connection is established."""
        try:
            if self.project_panel:
                self.project_panel.refresh_projects()
                self.project_panel.refresh_classes()
            self._sync_all()
            if self.project_panel:
                self.project_panel._refresh_sources()
                self.project_panel._refresh_models()
            if self.standalone_panel:
                self.standalone_panel.on_connected()
            self.iface.messageBar().pushMessage(
                PLUGIN_NAME,
                "Connected. Loaded project data from backend.",
                level=0, duration=3,
            )
        except Exception:
            pass

    def _activate_region_tool(self) -> None:
        """Activate the region drawing tool."""
        self.canvas.setMapTool(self.region_tool)

    def _activate_polygon_tool(self) -> None:
        """Activate the manual polygon annotation tool."""
        self.canvas.setMapTool(self.polygon_tool)

    def _activate_sam_tool(self) -> None:
        """Activate the SAM3 interactive labeling tool."""
        self.canvas.setMapTool(self.sam_tool)

    def _activate_aoi_tool(self) -> None:
        """Activate the AOI drawing tool for the training inference panel."""
        self.canvas.setMapTool(self.aoi_tool)

    def _activate_aoi_tool_standalone(self) -> None:
        """Activate the AOI drawing tool for the standalone inference panel."""
        self.canvas.setMapTool(self.aoi_tool_standalone)

    def _on_sam_session_started(self, image_path: str) -> None:
        """Update SAM tool with image extent info when a session starts."""
        canvas = self.iface.mapCanvas()
        extent = canvas.extent()
        width = canvas.width()
        height = canvas.height()

        try:
            session = self.connection_panel.client.sam_session()
            if session.get("active"):
                img_size = session.get("image_size", [width, height])
                width = img_size[0]
                height = img_size[1]
        except Exception:
            pass

        self.sam_tool.set_image_info(extent, width, height)

    def _sync_all(self) -> None:
        """Single sync point: refresh QGIS layers AND panel region list.

        Called after any data mutation (region created, annotation saved,
        mask accepted, region deleted, etc.).
        """
        class_colors = None
        class_names = None
        if self.project_panel:
            class_colors = {
                c.class_id: c.color
                for c in self.project_panel.class_manager.classes
            }
            class_names = {
                c.class_id: c.name
                for c in self.project_panel.class_manager.classes
            }
        if self.label_manager:
            self.label_manager.sync_regions()
            self.label_manager.sync_annotations(
                class_colors=class_colors, class_names=class_names
            )
        if self.project_panel:
            self.project_panel.refresh_regions()

    def _on_map_tool_changed(self, new_tool, old_tool=None) -> None:
        """Uncheck panel tool buttons when user switches to a non-plugin tool."""
        if self.project_panel and new_tool not in (
            self.polygon_tool, self.sam_tool, self.region_tool
        ):
            self.project_panel.deactivate_tool_buttons()
        if self.project_panel and new_tool is not self.aoi_tool:
            self.project_panel._draw_aoi_btn.setChecked(False)
        if self.standalone_panel and new_tool is not self.aoi_tool_standalone:
            self.standalone_panel.deactivate_aoi_button()
