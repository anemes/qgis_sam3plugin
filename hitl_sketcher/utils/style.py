"""QML style generation from class definitions."""

from __future__ import annotations

from typing import List


def generate_annotation_style(classes: List[dict]) -> str:
    """Generate QML style XML for annotation layer based on class definitions.

    Args:
        classes: List of {"class_id": int, "name": str, "color": str}

    Returns:
        QML style string.
    """
    rules = []
    for cls in classes:
        color = cls["color"].lstrip("#")
        r, g, b = int(color[:2], 16), int(color[2:4], 16), int(color[4:6], 16)
        rules.append(
            f'<rule filter="class_id = {cls["class_id"]}" symbol="{cls["class_id"]}" '
            f'label="{cls["name"]}"/>'
        )

    # Minimal QML — QGIS will use default rendering if this isn't applied
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<qgis>
  <renderer-v2 type="RuleRenderer">
    <rules>
      {''.join(rules)}
    </rules>
  </renderer-v2>
</qgis>"""
