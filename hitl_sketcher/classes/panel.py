"""Class manager dock widget with color pickers and keyboard shortcuts."""

from __future__ import annotations

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QColorDialog,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .manager import ClassManager


class ClassPanel(QDockWidget):
    """Dock widget for managing segmentation classes."""

    class_changed = pyqtSignal(int)  # emits active class_id

    def __init__(self, iface, client, parent=None):
        super().__init__("Classes", parent)
        self.iface = iface
        self.client = client
        self.manager = ClassManager()
        self._selected_class_id = None

        container = QWidget()
        layout = QVBoxLayout(container)

        # Class list
        self.class_list = QListWidget()
        self.class_list.currentItemChanged.connect(self._on_selection_changed)
        layout.addWidget(self.class_list)

        # Add class row
        add_row = QHBoxLayout()
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Class name")
        add_row.addWidget(self.name_input)

        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add_class)
        add_row.addWidget(add_btn)

        layout.addLayout(add_row)

        # Remove button
        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(self._remove_class)
        layout.addWidget(remove_btn)

        # Sync button
        sync_btn = QPushButton("Sync with Backend")
        sync_btn.clicked.connect(self._sync_classes)
        layout.addWidget(sync_btn)

        # Info
        self.info_label = QLabel("Add classes to start labeling (shortcuts: 1-9)")
        layout.addWidget(self.info_label)

        layout.addStretch()
        self.setWidget(container)

        self._refresh_list()

    @property
    def selected_class_id(self) -> int:
        return self._selected_class_id or 2

    def _add_class(self) -> None:
        name = self.name_input.text().strip()
        if not name:
            return
        self.manager.add_class(name)
        self.name_input.clear()
        self._refresh_list()

    def _remove_class(self) -> None:
        current = self.class_list.currentItem()
        if current:
            class_id = current.data(Qt.UserRole)
            self.manager.remove_class(class_id)
            self._refresh_list()

    def _refresh_list(self) -> None:
        self.class_list.clear()
        # Background (always present, not removable)
        bg_item = QListWidgetItem("1: background")
        bg_item.setData(Qt.UserRole, 1)
        bg_item.setForeground(QColor("#888888"))
        bg_item.setFlags(bg_item.flags() & ~Qt.ItemIsSelectable)
        self.class_list.addItem(bg_item)

        for i, cls in enumerate(self.manager.classes):
            shortcut = i + 2 if i < 8 else ""
            label = f"{shortcut}: {cls.name}" if shortcut else cls.name
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, cls.class_id)
            item.setForeground(QColor(cls.color))
            self.class_list.addItem(item)

        self.info_label.setText(f"{len(self.manager.classes)} classes defined")

    def _on_selection_changed(self, current, previous) -> None:
        if current:
            self._selected_class_id = current.data(Qt.UserRole)
            self.class_changed.emit(self._selected_class_id)

    def _sync_classes(self) -> None:
        """Sync class definitions with the backend."""
        try:
            self.client.set_classes(self.manager.to_dicts())
            self.info_label.setText("Classes synced with backend")
        except Exception as e:
            self.info_label.setText(f"Sync failed: {e}")
