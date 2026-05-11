# EasySegment — QGIS Plugin

QGIS plugin for interactive human-in-the-loop segmentation labeling. Connects to the EasySegment backend for SAM3-assisted labeling, model training, and tiled inference on geospatial imagery.

## Requirements

- QGIS 3.28+
- Running EasySegment backend (see [backend README](../backend/README.md))

## Install

### Option A: Symlink (development)

```bash
# Linux
ln -s /path/to/qgis_plugin/hitl_sketcher \
  ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/hitl_sketcher

# macOS
ln -s /path/to/qgis_plugin/hitl_sketcher \
  ~/Library/Application\ Support/QGIS/QGIS3/profiles/default/python/plugins/hitl_sketcher

# Windows (PowerShell, run as admin)
New-Item -ItemType SymbolicLink `
  -Path "$env:APPDATA\QGIS\QGIS3\profiles\default\python\plugins\hitl_sketcher" `
  -Target "C:\path\to\qgis_plugin\hitl_sketcher"
```

### Option B: Copy

Copy the `hitl_sketcher/` directory into your QGIS plugins folder (paths above).

### Enable

1. Open QGIS
2. Plugins → Manage and Install Plugins
3. Find **EasySegment** and enable it
4. The toolbar and dock panels will appear on the right side

## Usage

### Training workflow (HITL loop)

Use the **Project**, **Backend Connection**, and **Inference (Training)** panels.

1. **Connect** — Click the Backend Connection panel, enter the URL (default `http://localhost:8000`), click Connect
2. **Create/select a project** — Use the project dropdown in the Project panel
3. **Add classes** — e.g. "building", "road", "vegetation"
4. **Draw a region** — Click "Draw Region" and draw a rectangle on the map. Everything inside = labeling area, outside = ignored during training
5. **Label with SAM3** — Click "SAM Click" tool, click on objects to generate masks, accept/reject
6. **Label manually** — Click "Draw Polygon", draw vertices with left-click, finish with right-click
7. **Train** — Trigger training from the Project panel or Gradio dashboard; the backend trains DINOv3-sat + UperNet on your labels
8. **Inference for review** — Use the Inference (Training) panel to run predictions on an AOI, then promote results back into the project for review and correction
9. **Correct & retrain** — Fix prediction errors with more labels, retrain iteratively

Results appear in the **EasySegment Annotations** layer group in the QGIS layer panel.

### Standalone inference (no training setup required)

Use the **Inference** panel.

1. **Connect** to the backend
2. **Select a model** from the catalogue dropdown — includes checkpoints from all projects and any globally registered models
3. **Choose a raster source:**
   - *XYZ Tile URL* — enter a tile URL template (e.g. `https://tile.openstreetmap.org/{z}/{x}/{y}.png`) and zoom level; draw an AOI on the map
   - *Capture Current Canvas* — renders the current QGIS viewport to a GeoTIFF and uses its extent as the AOI
4. **Run Inference** — progress bar shows tile completion
5. Results appear in the **EasySegment Predictions** layer group, named `{classes} — {timestamp}`
6. Previous results are listed in the **Saved Results** section and can be reloaded after a QGIS restart

Standalone results are automatically persisted to the backend's `_inference` project and survive session restarts.

## Plugin Structure

```
hitl_sketcher/
  plugin.py               Entry point, toolbar, dock wiring, signal hub
  connection/
    client.py             REST client for all backend API calls
    panel.py              Backend URL + connect button dock widget
  labeling/
    project_panel.py      Unified dock: project/class/region/SAM3 controls
    polygon_tool.py       Manual polygon labeling map tool
    sam_tool.py           SAM3 click/box interactive map tool
    region_tool.py        Region boundary drawing map tool
    label_layer.py        QGIS memory layers synced from backend (EasySegment Annotations)
  raster/
    capture.py            Renders QGIS canvas viewport to GeoTIFF
  prediction/
    inference_panel.py    Inference (Training) dock: training-linked inference + promote to review
    inference_tool.py     AOI rectangle drawing map tool (shared by both inference panels)
    standalone_panel.py   Inference dock: model catalogue, standalone inference, saved results
    viewer.py             Loads prediction rasters/vectors as styled QGIS layers
  classes/
    manager.py            In-memory class definitions (class_id, name, color)
```

## Layer Groups

The plugin places layers into named groups in the QGIS layer panel to keep training data and inference results separate:

| Group | Contents |
|---|---|
| `EasySegment Annotations` | Region boundaries and annotation polygons from the labeling/training workflow |
| `EasySegment Predictions` | Vector prediction layers from standalone inference runs |

## License

This plugin is licensed under the **GNU General Public License v2.0 or later** (GPL-2.0+). See [LICENSE](LICENSE) for the full text.

Backend repository: https://github.com/anemes/backend_samdino
