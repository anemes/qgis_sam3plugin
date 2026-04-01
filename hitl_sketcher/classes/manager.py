"""Class CRUD logic: create, edit, delete custom segmentation classes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class SegClassDef:
    """Local class definition."""

    class_id: int  # starts at 2 (1 = background)
    name: str
    color: str  # hex "#RRGGBB"


class ClassManager:
    """Manages segmentation class definitions.

    Background (class_id=1) is always implicit and cannot be deleted.
    User-defined classes start at class_id=2.
    """

    DEFAULT_COLORS = [
        "#FF0000", "#00FF00", "#0000FF", "#FFFF00",
        "#FF00FF", "#00FFFF", "#FF8800", "#8800FF",
        "#008800", "#880000",
    ]

    def __init__(self):
        self._classes: List[SegClassDef] = []
        self._next_id = 2

    @property
    def classes(self) -> List[SegClassDef]:
        return list(self._classes)

    @property
    def active_class(self) -> Optional[SegClassDef]:
        return self._classes[0] if self._classes else None

    def add_class(self, name: str, color: Optional[str] = None) -> SegClassDef:
        """Add a new class. Returns the created class."""
        if color is None:
            idx = len(self._classes) % len(self.DEFAULT_COLORS)
            color = self.DEFAULT_COLORS[idx]

        cls = SegClassDef(class_id=self._next_id, name=name, color=color)
        self._classes.append(cls)
        self._next_id += 1
        return cls

    def remove_class(self, class_id: int) -> bool:
        """Remove a class by ID. Returns True if removed."""
        before = len(self._classes)
        self._classes = [c for c in self._classes if c.class_id != class_id]
        return len(self._classes) < before

    def update_class(self, class_id: int, name: Optional[str] = None, color: Optional[str] = None) -> bool:
        """Update a class's name or color."""
        for c in self._classes:
            if c.class_id == class_id:
                if name is not None:
                    c.name = name
                if color is not None:
                    c.color = color
                return True
        return False

    def get_class(self, class_id: int) -> Optional[SegClassDef]:
        for c in self._classes:
            if c.class_id == class_id:
                return c
        return None

    def to_dicts(self) -> List[dict]:
        """Convert to list of dicts for API sync."""
        return [{"class_id": c.class_id, "name": c.name, "color": c.color} for c in self._classes]

    def from_dicts(self, data: List[dict]) -> None:
        """Load from list of dicts (from API)."""
        self._classes = [SegClassDef(**d) for d in data]
        if self._classes:
            self._next_id = max(c.class_id for c in self._classes) + 1
        else:
            self._next_id = 2
