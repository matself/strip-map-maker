# SPDX-License-Identifier: GPL-2.0-or-later
"""Dock widget for Strip Map Maker (code only, no .ui file, so no uic differences Qt5/Qt6)."""
from pathlib import Path

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsProject,
    QgsVectorLayer,
)
from qgis.gui import QgsMapCanvasItem, QgsMapLayerComboBox, QgsRubberBand
from qgis.PyQt.QtCore import QRectF, Qt, QTimer
from qgis.PyQt.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .core.guide import GuideError, chain_lines, single_line, smooth_line
from .core.placement import PlacementError, place_frames
from .core.series import SeriesError, Setup, add_to_project, create_series
from .maptool import DrawGuideTool

PREVIEW_DELAY_MS = 300


class FrameNumbers(QgsMapCanvasItem):
    """Draws the sheet numbers of the preview at the frame centres."""

    def __init__(self, canvas):
        super().__init__(canvas)
        self._canvas = canvas
        self._labels = []  # (QgsPointXY in canvas CRS, text)
        canvas.extentsChanged.connect(self.update)

    def set_labels(self, labels):
        self._labels = labels
        self.update()

    def boundingRect(self):  # noqa: N802
        return QRectF(0, 0, self._canvas.width(), self._canvas.height())

    def updatePosition(self):  # noqa: N802
        self.update()

    def paint(self, painter, option=None, widget=None):
        if not self._labels:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont("Arial", 16)
        font.setBold(True)
        for point, text in self._labels:
            pos = self.toCanvasCoordinates(point)
            path = QPainterPath()
            path.addText(0, 0, font, text)
            rect = path.boundingRect()
            path.translate(pos.x() - rect.center().x(), pos.y() - rect.center().y())
            painter.setPen(QPen(QColor(255, 255, 255), 4))
            painter.drawPath(path)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(20, 40, 120))
            painter.drawPath(path)


class StripMapMakerDockWidget(QDockWidget):
    def __init__(self, iface, parent=None):
        super().__init__("Strip Map Maker", parent)
        self.setObjectName("StripMapMakerDockWidget")
        self.iface = iface
        self.canvas = iface.mapCanvas()

        self._source = None  # the guide as chosen (unsmoothed)
        self._crs = QgsCoordinateReferenceSystem()
        self._placement = None
        self._guide = None  # the smoothed guide the frames follow
        self._previous_tool = None
        self._draw_tool = DrawGuideTool(self.canvas)
        self._draw_tool.finished.connect(self._drawn)
        self._draw_tool.cancelled.connect(self._stop_drawing)

        self._frames_band = QgsRubberBand(self.canvas, Qgis.GeometryType.Polygon)
        self._frames_band.setColor(QColor(30, 90, 200, 200))
        self._frames_band.setFillColor(QColor(30, 90, 200, 25))
        self._frames_band.setWidth(2)
        self._guide_band = QgsRubberBand(self.canvas, Qgis.GeometryType.Line)
        self._guide_band.setColor(QColor(220, 30, 30))
        self._guide_band.setWidth(2)
        self._numbers = FrameNumbers(self.canvas)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(PREVIEW_DELAY_MS)
        self._timer.timeout.connect(self._update_preview)

        self._build_ui()

    # ---- UI -----------------------------------------------------------------------------

    def _build_ui(self):
        content = QWidget(self)
        layout = QVBoxLayout(content)

        guide_box = QGroupBox("1. Guide line")
        guide_form = QFormLayout(guide_box)
        self.layer_combo = QgsMapLayerComboBox()
        self.layer_combo.setFilters(Qgis.LayerFilter.LineLayer)
        use_selected = QPushButton("Use selected lines")
        use_selected.clicked.connect(self._use_selected)
        self.draw_button = QPushButton("Draw line on map")
        self.draw_button.clicked.connect(self._start_drawing)
        self.guide_label = QLabel("No guide line yet.")
        self.guide_label.setWordWrap(True)
        guide_form.addRow("Layer:", self.layer_combo)
        guide_form.addRow(use_selected)
        guide_form.addRow(self.draw_button)
        clear_button = QPushButton("Clear and start over")
        clear_button.clicked.connect(self._reset)
        guide_form.addRow(self.guide_label)
        guide_form.addRow(clear_button)
        layout.addWidget(guide_box)

        setup_box = QGroupBox("2. Sheets")
        setup_form = QFormLayout(setup_box)
        self.width_spin = self._spin(QDoubleSpinBox(), 1, 10000, 280, " mm")
        self.height_spin = self._spin(QDoubleSpinBox(), 1, 10000, 180, " mm")
        self.scale_spin = self._spin(QSpinBox(), 1, 10000000, 1000, "", "1 : ")
        self.scale_spin.setSingleStep(500)
        self.overlap_spin = self._spin(QDoubleSpinBox(), 0, 90, 10, " %")
        self.smooth_spin = self._spin(QDoubleSpinBox(), 0, 100000, 0, " m")
        self.smooth_spin.setToolTip("Averaging length; 0 follows the line exactly.")
        setup_form.addRow("Width:", self.width_spin)
        setup_form.addRow("Height:", self.height_spin)
        setup_form.addRow("Scale:", self.scale_spin)
        setup_form.addRow("Overlap:", self.overlap_spin)
        setup_form.addRow("Smoothing:", self.smooth_spin)
        self.reverse_check = QCheckBox("Reverse sheet numbering")
        self.reverse_check.setToolTip("Number the sheets from the other end of the guide line.")
        self.reverse_check.toggled.connect(self._schedule_preview)
        setup_form.addRow(self.reverse_check)
        layout.addWidget(setup_box)

        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        create_box = QGroupBox("3. Create")
        create_form = QFormLayout(create_box)
        self.name_edit = QLineEdit("Strip map")
        self.create_button = QPushButton("Create series...")
        self.create_button.setEnabled(False)
        self.create_button.clicked.connect(self._create)
        create_form.addRow("Name:", self.name_edit)
        create_form.addRow(self.create_button)
        layout.addWidget(create_box)

        layout.addStretch()
        self.setWidget(content)

    def _spin(self, spin, low, high, value, suffix="", prefix=""):
        spin.setRange(low, high)
        spin.setValue(value)
        if suffix:
            spin.setSuffix(suffix)
        if prefix:
            spin.setPrefix(prefix)
        spin.valueChanged.connect(self._schedule_preview)
        return spin

    # ---- guide input --------------------------------------------------------------------

    def _use_selected(self):
        layer = self.layer_combo.currentLayer()
        if not isinstance(layer, QgsVectorLayer):
            self._message("Pick a line layer first.")
            return
        features = layer.selectedFeatures()
        if not features:
            self._message("Select one or more lines in the layer first.")
            return
        try:
            result = chain_lines([f.geometry() for f in features], tolerance=self._tolerance())
        except GuideError as error:
            self._message(str(error))
            return
        note = f" ({result.pieces} lines joined)" if result.pieces > 1 else ""
        self._set_guide(result.geometry, layer.crs(), note)

    def _start_drawing(self):
        self._previous_tool = self.canvas.mapTool()
        self.canvas.setMapTool(self._draw_tool)
        self.draw_button.setText("Drawing: left click adds, right click finishes")

    def _stop_drawing(self):
        self.draw_button.setText("Draw line on map")
        if self._previous_tool is not None:
            self.canvas.setMapTool(self._previous_tool)
        else:
            self.canvas.unsetMapTool(self._draw_tool)

    def _drawn(self, geometry):
        self._stop_drawing()
        self._set_guide(geometry, self.canvas.mapSettings().destinationCrs(), " (drawn)")

    def _tolerance(self):
        return 1.0 if not self._crs.isGeographic() else 1e-5

    def _set_guide(self, geometry, crs, note=""):
        if crs.isGeographic():
            self._message(
                "The guide is in a geographic CRS (degrees). Reproject the layer, or set the "
                "project to a projected CRS in metres, and try again."
            )
            return
        try:
            self._source = single_line(geometry)
        except GuideError as error:
            self._message(str(error))
            return
        self._crs = crs
        self.guide_label.setText(f"Guide: {self._source.length():,.0f} m{note}")
        self._schedule_preview()

    # ---- preview ------------------------------------------------------------------------

    def _ground(self):
        scale = self.scale_spin.value()
        return self.width_spin.value() * scale / 1000, self.height_spin.value() * scale / 1000

    def _schedule_preview(self, *_):
        self._timer.start()

    def _clear_preview(self):
        self._frames_band.reset(Qgis.GeometryType.Polygon)
        self._guide_band.reset(Qgis.GeometryType.Line)
        self._numbers.set_labels([])

    def _show_numbers(self, frames):
        to_canvas = QgsCoordinateTransform(
            self._crs, self.canvas.mapSettings().destinationCrs(), QgsProject.instance()
        )
        self._numbers.set_labels(
            [(to_canvas.transform(QgsPointXY(f.x, f.y)), str(f.id)) for f in frames]
        )

    def _update_preview(self):
        self._placement = None
        self.create_button.setEnabled(False)
        width, height = self._ground()
        text = f"Sheet covers {width:,.0f} x {height:,.0f} m on the ground."
        if self._source is None:
            self.info_label.setText(text)
            return
        try:
            guide = smooth_line(self._source, self.smooth_spin.value())
            placement = place_frames(
                guide, width, height, self.overlap_spin.value(), cover=self._source
            )
        except (GuideError, PlacementError) as error:
            self._clear_preview()
            self.info_label.setText(str(error))
            return
        if self.reverse_check.isChecked():
            count = len(placement.frames)
            for frame in placement.frames:
                frame.id = count + 1 - frame.id
        self._placement = placement
        self._guide = guide
        self._clear_preview()
        for frame in placement.frames:
            self._frames_band.addGeometry(frame.geometry, self._crs)
        self._show_numbers(placement.frames)
        self._guide_band.setToGeometry(guide, self._crs)
        text += f"\n{len(placement.frames)} sheets, overlap {placement.overlap_pct:.1f} %."
        if placement.uncovered:
            text += "\nPart of the line is outside the sheets: use a larger sheet or scale."
        self.info_label.setText(text)
        self.create_button.setEnabled(True)

    # ---- create -------------------------------------------------------------------------

    def _create(self):
        if self._placement is None:
            return
        name = self.name_edit.text().strip() or "Strip map"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save series", f"{name}.gpkg", "GeoPackage (*.gpkg)"
        )
        if not path:
            return
        if not path.lower().endswith(".gpkg"):
            path += ".gpkg"
        setup = Setup(
            name=name,
            width_mm=self.width_spin.value(),
            height_mm=self.height_spin.value(),
            scale=self.scale_spin.value(),
            overlap_pct=self.overlap_spin.value(),
            smoothing_m=self.smooth_spin.value(),
        )
        try:
            create_series(Path(path), self._guide, self._crs, setup, self._placement)
            add_to_project(path, QgsProject.instance())
        except SeriesError as error:
            self._message(str(error))
            return
        self._reset(deselect=False)
        self.info_label.setText(f"Created {Path(path).name}.")

    def _reset(self, *_, deselect=True):
        """Forget the guide line and the preview so a new series can be started."""
        self._timer.stop()
        if self.canvas.mapTool() is self._draw_tool:
            self._stop_drawing()
        self._clear_preview()
        self._source = None
        self._guide = None
        self._placement = None
        self.create_button.setEnabled(False)
        self.guide_label.setText("No guide line yet.")
        self.info_label.setText("")
        layer = self.layer_combo.currentLayer()
        if deselect and isinstance(layer, QgsVectorLayer):
            layer.removeSelection()

    def _message(self, text):
        self.iface.messageBar().pushMessage(
            "Strip Map Maker", text, level=Qgis.MessageLevel.Warning
        )

    def cleanup(self):
        """Remove everything this dock put on the canvas."""
        self._clear_preview()
        self.canvas.scene().removeItem(self._frames_band)
        self.canvas.scene().removeItem(self._guide_band)
        self.canvas.scene().removeItem(self._numbers)

    def closeEvent(self, event):  # noqa: N802
        self._clear_preview()
        super().closeEvent(event)

