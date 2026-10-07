"""Dock widget for Strip Map Maker."""
from qgis.PyQt.QtWidgets import QDockWidget, QLabel, QVBoxLayout, QWidget


class StripMapMakerDockWidget(QDockWidget):
    """Minimal code-only dock widget (no .ui file, so no uic differences Qt5/Qt6)."""

    def __init__(self, parent=None):
        super().__init__("Strip Map Maker", parent)
        self.setObjectName("StripMapMakerDockWidget")

        content = QWidget(self)
        layout = QVBoxLayout(content)
        layout.addWidget(QLabel("Hello from Strip Map Maker!"))
        layout.addStretch()
        self.setWidget(content)
