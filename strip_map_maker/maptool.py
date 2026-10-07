# SPDX-License-Identifier: GPL-2.0-or-later
"""Canvas tool for drawing the guide line."""
from qgis.core import Qgis, QgsGeometry, QgsPointXY
from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor


class DrawGuideTool(QgsMapTool):
    """Left click adds a vertex, right click finishes, Esc cancels."""

    finished = pyqtSignal(QgsGeometry)  # in the map canvas CRS
    cancelled = pyqtSignal()

    def __init__(self, canvas):
        super().__init__(canvas)
        self._points = []
        self._band = QgsRubberBand(canvas, Qgis.GeometryType.Line)
        self._band.setColor(QColor(220, 30, 30))
        self._band.setWidth(2)

    def activate(self):
        super().activate()
        self._points = []
        self._band.reset(Qgis.GeometryType.Line)

    def deactivate(self):
        self._band.reset(Qgis.GeometryType.Line)
        super().deactivate()

    def canvasReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._points.append(QgsPointXY(event.mapPoint()))
            self._band.addPoint(QgsPointXY(event.mapPoint()))
        elif event.button() == Qt.MouseButton.RightButton:
            if len(self._points) >= 2:
                self.finished.emit(QgsGeometry.fromPolylineXY(self._points))
            else:
                self.cancelled.emit()

    def canvasMoveEvent(self, event):  # noqa: N802
        if self._points:
            self._band.movePoint(QgsPointXY(event.mapPoint()))

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            event.accept()
