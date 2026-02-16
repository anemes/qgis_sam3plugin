# HITL Sketcher — QGIS Plugin

QGIS plugin for interactive human-in-the-loop segmentation labeling. Connects to the HITL segmentation backend for SAM3-assisted labeling, model training, and tiled inference on geospatial imagery.

## Requirements

- QGIS 3.28+
- Running HITL segmentation backend (see backend repo)

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
2. Plugins -> Manage and Install Plugins
3. Find **HITL Sketcher** and enable it
4. The toolbar and dock panel will appear

## Usage

1. **Connect** — Click the backend connection button, enter the URL (default `http://localhost:8000`), click Connect
2. **Create/select a project** — Use the project dropdown in the panel
3. **Add classes** — e.g. "building", "road", "vegetation"
4. **Draw a region** — Click "Draw Region" and draw a rectangle on the map. Everything inside = labeling area, outside = ignored during training
5. **Label with SAM3** — Click "SAM Click" tool, click on objects to generate masks, accept/reject
6. **Label manually** — Click "Draw Polygon", draw vertices with left-click, finish with right-click
7. **Train** — Trigger training from the API or dashboard; the backend trains DINOv3-sat + UperNet on your labels
8. **Infer** — Run tiled inference on large areas, view predictions as a QGIS layer
9. **Correct & retrain** — Fix prediction errors with more labels, retrain iteratively

## Plugin Structure

```
hitl_sketcher/
  plugin.py           Main plugin entry, toolbar, signal wiring
  connection/
    client.py          REST client for backend API
    panel.py           Connection settings UI
  labeling/
    project_panel.py   Project/region/annotation management panel
    polygon_tool.py    Manual polygon labeling map tool
    sam_tool.py        SAM3 click/box interactive map tool
    region_tool.py     Region drawing map tool
    label_layer.py     QGIS memory layer sync from backend
  raster/
    capture.py         GeoTIFF export from QGIS canvas
  prediction/
    inference_tool.py  Inference triggering
    viewer.py          Prediction layer display
  classes/
    manager.py         Class definition management
    panel.py           Class editing UI
  utils/
    layers.py          Layer helper utilities
    style.py           Symbology/styling helpers
```

## License

This plugin is licensed under the **GNU General Public License v2.0 or later** (GPL-2.0+). See [LICENSE](LICENSE) for the full text.

The HITL segmentation backend (separate repo) is licensed under CC BY-NC-4.0.
