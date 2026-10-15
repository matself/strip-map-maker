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
from qgis.PyQt.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QTransform
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .core.atlas import (
    MIN_BAND,
    AtlasError,
    AtlasOptions,
    build_atlas,
    page_band,
)
from .core.guide import GuideError, chain_lines, single_line, smooth_line
from .core.placement import PlacementError, place_frames, reverse_placement
from .core.series import (
    ROLE_PROPERTY,
    SeriesError,
    Setup,
    add_to_project,
    create_series,
    layout_rotation,
    read_setup,
)
from .maptool import DrawGuideTool

PREVIEW_DELAY_MS = 300
DEFAULT_GAP_M = 5.0  # gaps between selected lines that are bridged
# Sheet sizes (mm) that fill a landscape page and leave a band below for the notes.
PAGE_PRESETS = {"A4 landscape": (277, 150), "A3 landscape": (400, 220)}


class FrameNumbers(QgsMapCanvasItem):
    """Draws the sheet numbers of the preview at the frame centres."""

    def __init__(self, canvas):
        super().__init__(canvas)
        self._canvas = canvas
        self._labels = []  # (QgsPointXY in canvas CRS, text, rotation in degrees clockwise)
        canvas.extentsChanged.connect(self.update)
        canvas.rotationChanged.connect(self.update)

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
        for point, text, angle in self._labels:
            pos = self.toCanvasCoordinates(point)
            path = QPainterPath()
            path.addText(0, 0, font, text)
            rect = path.boundingRect()
            path.translate(-rect.center().x(), -rect.center().y())
            turn = QTransform()
            turn.translate(pos.x(), pos.y())
            turn.rotate(angle + self._canvas.rotation())
            path = turn.map(path)
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
        self._from_selection = False
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
        project = QgsProject.instance()
        for layer in project.mapLayers().values():
            self._watch_layer(layer)
        project.layerWasAdded.connect(self._watch_layer)

    # ---- UI -----------------------------------------------------------------------------

    def _build_ui(self):
        tabs = QTabWidget(self)
        series_page = QWidget()
        layout = QVBoxLayout(series_page)

        setup_box = QGroupBox(self.tr("1. Sheets"))
        setup_form = QFormLayout(setup_box)
        self.width_spin = self._spin(QDoubleSpinBox(), 1, 10000, 280, " mm")
        self.height_spin = self._spin(QDoubleSpinBox(), 1, 10000, 180, " mm")
        for spin in (self.width_spin, self.height_spin):
            spin.valueChanged.connect(self._sheet_size_edited)
        self.scale_spin = self._spin(QSpinBox(), 1, 10000000, 1000, "", "1 : ")
        self.scale_spin.setSingleStep(500)
        self.overlap_spin = self._spin(QDoubleSpinBox(), 0, 90, 10, " %")
        self.smooth_spin = self._spin(QDoubleSpinBox(), 0, 100000, 0, " m")
        self.smooth_spin.setToolTip(self.tr("Averaging length; 0 follows the line exactly."))
        self.preset_combo = QComboBox()
        self.preset_combo.addItem(self.tr("Custom"), "")
        self.preset_combo.addItem(self.tr("A4 landscape"), "A4 landscape")
        self.preset_combo.addItem(self.tr("A3 landscape"), "A3 landscape")
        self.preset_combo.setToolTip(
            self.tr(
                "Sheet sizes that fill the page and leave room below for the sheet number, "
                "scale bar and overview map in the atlas."
            )
        )
        self.preset_combo.activated.connect(self._apply_preset)
        setup_form.addRow(self.tr("Fit to page:"), self.preset_combo)
        setup_form.addRow(self.tr("Width:"), self.width_spin)
        setup_form.addRow(self.tr("Height:"), self.height_spin)
        setup_form.addRow(self.tr("Scale:"), self.scale_spin)
        setup_form.addRow(self.tr("Overlap:"), self.overlap_spin)
        self.reverse_button = QPushButton(self.tr("Reverse sheet numbering"))
        self.reverse_button.setCheckable(True)
        self.reverse_button.setToolTip(
            self.tr(
                "Number the sheets from the other end of the guide line. Renumbers the sheets "
                "shown; their positions stay."
            )
        )
        self.reverse_button.toggled.connect(self._reverse_numbers)
        layout.addWidget(setup_box)

        guide_box = QGroupBox(self.tr("2. Guide line"))
        guide_form = QFormLayout(guide_box)
        self.layer_combo = QgsMapLayerComboBox()
        self.layer_combo.setFilters(Qgis.LayerFilter.LineLayer)
        use_selected = QPushButton(self.tr("Use selected lines"))
        use_selected.clicked.connect(self._use_selected)
        self.draw_button = QPushButton(self.tr("Draw line on map"))
        self.draw_button.clicked.connect(self._start_drawing)
        self.guide_label = QLabel(self.tr("No guide line yet."))
        self.guide_label.setWordWrap(True)
        guide_form.addRow(self.tr("Layer:"), self.layer_combo)
        guide_form.addRow(self.tr("Smoothing:"), self.smooth_spin)
        self.gap_spin = QDoubleSpinBox()
        self.gap_spin.setRange(0, 10000)
        self.gap_spin.setDecimals(1)
        self.gap_spin.setValue(DEFAULT_GAP_M)
        self.gap_spin.setSuffix(" m")
        self.gap_spin.setToolTip(
            self.tr(
                "Selected lines whose ends are this close are joined with a straight segment, "
                "so small digitising gaps do not stop the route."
            )
        )
        self.gap_spin.valueChanged.connect(self._gap_changed)
        guide_form.addRow(self.tr("Join gaps up to:"), self.gap_spin)
        guide_form.addRow(use_selected)
        guide_form.addRow(self.draw_button)
        self.finish_button = QPushButton(self.tr("Finish line"))
        self.finish_button.setVisible(False)
        self.finish_button.clicked.connect(self._draw_tool.finish)
        guide_form.addRow(self.finish_button)
        clear_button = QPushButton(self.tr("Clear and start over"))
        clear_button.clicked.connect(self._reset)
        guide_form.addRow(self.guide_label)
        guide_form.addRow(self.reverse_button)
        guide_form.addRow(clear_button)
        layout.addWidget(guide_box)

        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        save_box = QGroupBox(self.tr("3. Save"))
        save_form = QFormLayout(save_box)
        self.name_edit = QLineEdit(self.tr("Strip map"))
        self.create_button = QPushButton(self.tr("Save series..."))
        self.create_button.setEnabled(False)
        self.create_button.clicked.connect(self._create)
        save_form.addRow(self.tr("Name:"), self.name_edit)
        save_form.addRow(self.create_button)
        layout.addWidget(save_box)

        layout.addStretch()
        tabs.addTab(series_page, self.tr("Series"))
        tabs.addTab(self._build_atlas_tab(), self.tr("Atlas"))
        tabs.currentChanged.connect(self._refresh_series_list)
        self.setWidget(tabs)

    def _build_atlas_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        box = QGroupBox(self.tr("Atlas from a saved series"))
        form = QFormLayout(box)
        self.series_combo = QComboBox()
        self.series_combo.currentIndexChanged.connect(self._atlas_check)
        open_button = QPushButton(self.tr("Open series file..."))
        open_button.clicked.connect(self._open_series)
        self.page_combo = QComboBox()
        self.page_combo.addItems(["A4", "A3"])
        self.page_combo.currentIndexChanged.connect(self._atlas_check)
        self.overview_check = QCheckBox(self.tr("Overview map"))
        self.overview_check.setChecked(True)
        self.scalebar_check = QCheckBox(self.tr("Scale bar"))
        self.scalebar_check.setChecked(True)
        self.arrow_check = QCheckBox(self.tr("North arrow"))
        self.arrow_check.setChecked(True)
        self.layout_name_edit = QLineEdit()
        self.layout_name_edit.setPlaceholderText(self.tr("Layout name (optional)"))
        self.atlas_info = QLabel("")
        self.atlas_info.setWordWrap(True)
        self.atlas_button = QPushButton(self.tr("Create atlas"))
        self.atlas_button.clicked.connect(self._create_atlas)
        form.addRow(self.tr("Series:"), self.series_combo)
        form.addRow(open_button)
        form.addRow(self.tr("Page (landscape):"), self.page_combo)
        form.addRow(self.overview_check)
        form.addRow(self.scalebar_check)
        form.addRow(self.arrow_check)
        form.addRow(self.tr("Name:"), self.layout_name_edit)
        form.addRow(self.atlas_info)
        form.addRow(self.atlas_button)
        layout.addWidget(box)
        layout.addStretch()
        QgsProject.instance().layersAdded.connect(self._refresh_series_list)
        QgsProject.instance().layersRemoved.connect(self._refresh_series_list)
        self._refresh_series_list()
        return page

    def _apply_preset(self, *_):
        size = PAGE_PRESETS.get(self.preset_combo.currentData())
        if size is None:
            return
        for spin, value in zip((self.width_spin, self.height_spin), size):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        self._schedule_preview()

    def _sheet_size_edited(self, *_):
        self.preset_combo.setCurrentIndex(0)  # back to "Custom"
        self._schedule_preview()

    def _spin(self, spin, low, high, value, suffix="", prefix=""):
        spin.setRange(low, high)
        spin.setValue(value)
        if suffix:
            spin.setSuffix(suffix)
        if prefix:
            spin.setPrefix(prefix)
        spin.valueChanged.connect(self._schedule_preview)
        return spin

    # ---- atlas --------------------------------------------------------------------------

    def _series_layers(self):
        return [
            layer
            for layer in QgsProject.instance().mapLayers().values()
            if layer.customProperty(ROLE_PROPERTY) == "frames"
        ]

    def _refresh_series_list(self, *_):
        current = self.series_combo.currentData()
        self.series_combo.blockSignals(True)
        self.series_combo.clear()
        for layer in self._series_layers():
            self.series_combo.addItem(layer.name(), layer.id())
        index = self.series_combo.findData(current)
        self.series_combo.setCurrentIndex(max(index, 0))
        self.series_combo.blockSignals(False)
        self._atlas_check()

    def _open_series(self):
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr("Open series"), "", "GeoPackage (*.gpkg)"
        )
        if not path:
            return
        try:
            layer = add_to_project(path, QgsProject.instance())
        except SeriesError as error:
            self._message(str(error))
            return
        self._refresh_series_list()
        self.series_combo.setCurrentIndex(self.series_combo.findData(layer.id()))

    def _selected_series(self):
        """(layer, setup) of the chosen series, or (None, None)."""
        layer = QgsProject.instance().mapLayer(self.series_combo.currentData() or "")
        if layer is None:
            return None, None
        try:
            return layer, read_setup(layer.source().split("|")[0])
        except SeriesError as error:
            self._message(str(error))
            return None, None

    def _atlas_check(self, *_):
        """Say whether the sheets fit the chosen page; enable the button accordingly."""
        self.atlas_button.setEnabled(False)
        layer, setup = self._selected_series()
        if layer is None:
            self.atlas_info.setText(
                self.tr("No series in the project. Save one, or open a series file.")
            )
            return
        text = self.tr("Sheets {width:g} x {height:g} mm, scale 1:{scale}.").format(
            width=setup.width_mm, height=setup.height_mm, scale=setup.scale
        )
        try:
            band = page_band(self.page_combo.currentText(), setup)
        except AtlasError as error:
            self.atlas_info.setText(f"{text}\n{error}")
            return
        if band < MIN_BAND:
            text += "\n" + self.tr(
                "Only {band:.0f} mm is left under the map: no room for the notes."
            ).format(band=band)
        else:
            text += "\n" + self.tr("{band:.0f} mm is left under the map for the notes.").format(
                band=band
            )
        self.atlas_info.setText(text)
        self.atlas_button.setEnabled(True)

    def _create_atlas(self):
        layer, setup = self._selected_series()
        if layer is None:
            return
        options = AtlasOptions(
            page=self.page_combo.currentText(),
            overview=self.overview_check.isChecked(),
            scale_bar=self.scalebar_check.isChecked(),
            north_arrow=self.arrow_check.isChecked(),
            name=self.layout_name_edit.text().strip(),
        )
        try:
            layout = build_atlas(QgsProject.instance(), layer, setup, options)
        except AtlasError as error:
            self._message(str(error))
            return
        self.atlas_info.setText(self.tr("Created the layout '{name}'.").format(name=layout.name()))
        self.iface.openLayoutDesigner(layout)

    # ---- guide input --------------------------------------------------------------------

    def _selection_layer(self):
        """The line layer holding the selection: the chosen layer, else any with one."""
        current = self.layer_combo.currentLayer()
        if isinstance(current, QgsVectorLayer) and current.selectedFeatureCount():
            return current
        for layer in QgsProject.instance().mapLayers().values():
            if (
                isinstance(layer, QgsVectorLayer)
                and layer.geometryType() == Qgis.GeometryType.Line
                and layer.selectedFeatureCount()
            ):
                return layer
        return current

    def _watch_layer(self, layer):
        if isinstance(layer, QgsVectorLayer) and layer.geometryType() == Qgis.GeometryType.Line:
            layer.selectionChanged.connect(self._selection_changed)

    def _selection_changed(self, selected, *_):
        """Pick up a new selection straight away; clearing one leaves the guide alone."""
        if selected and self.canvas.mapTool() is not self._draw_tool:
            layer = self.sender()
            if isinstance(layer, QgsVectorLayer):
                self.layer_combo.setLayer(layer)
            self._use_selected(quiet=True)

    def _use_selected(self, *_, quiet=False):
        layer = self._selection_layer()
        if not isinstance(layer, QgsVectorLayer):
            if not quiet:
                self._message(self.tr("Pick a line layer first."))
            return
        features = layer.selectedFeatures()
        if not features:
            if not quiet:
                self._message(self.tr("Select one or more lines in the layer first."))
            return
        self.layer_combo.setLayer(layer)
        try:
            result = chain_lines([f.geometry() for f in features], tolerance=self._tolerance())
        except GuideError as error:
            self._message(str(error))
            return
        note = ""
        if result.pieces > 1:
            note = " (" + self.tr("{count} lines joined").format(count=result.pieces) + ")"
        if self._set_guide(result.geometry, layer.crs(), note):
            self._from_selection = True

    def _start_drawing(self):
        self._previous_tool = self.canvas.mapTool()
        self.canvas.setMapTool(self._draw_tool)
        self.draw_button.setText(self.tr("Drawing: left click adds, right click finishes"))
        self.finish_button.setVisible(True)

    def _stop_drawing(self):
        self.draw_button.setText(self.tr("Draw line on map"))
        self.finish_button.setVisible(False)
        if self._previous_tool is not None:
            self.canvas.setMapTool(self._previous_tool)
        else:
            self.canvas.unsetMapTool(self._draw_tool)

    def _drawn(self, geometry):
        self._stop_drawing()
        note = " (" + self.tr("drawn") + ")"
        if self._set_guide(geometry, self.canvas.mapSettings().destinationCrs(), note):
            self._from_selection = False

    def _tolerance(self):
        return self.gap_spin.value()

    def _gap_changed(self, *_):
        """Rebuild the guide from the selection when the gap tolerance changes."""
        if self._from_selection:
            self._use_selected(quiet=True)

    def _reverse_numbers(self, *_):
        """Flip the numbering of the sheets on show."""
        if self._placement is None:
            return
        reverse_placement(self._placement, self._guide.length())
        self._show_numbers(self._placement.frames)

    def _set_guide(self, geometry, crs, note=""):
        """Make ``geometry`` the guide; returns False (and says why) if it cannot be used."""
        if crs.isGeographic():
            self._message(
                self.tr(
                    "The guide is in a geographic CRS (degrees). Reproject the layer, or set "
                    "the project to a projected CRS in metres, and try again."
                )
            )
            return False
        try:
            self._source = single_line(geometry)
        except GuideError as error:
            self._message(str(error))
            return False
        self._crs = crs
        self.guide_label.setText(
            self.tr("Guide: {length:,.0f} m{note}").format(
                length=self._source.length(), note=note
            )
        )
        self._schedule_preview()
        return True

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
            [
                (
                    to_canvas.transform(QgsPointXY(f.x, f.y)),
                    str(f.id),
                    (360 - layout_rotation(f.azi)) % 360,  # as the saved sheet numbers
                )
                for f in frames
            ]
        )

    def _update_preview(self):
        self._placement = None
        self.create_button.setEnabled(False)
        width, height = self._ground()
        text = self.tr("Sheet covers {width:,.0f} x {height:,.0f} m on the ground.").format(
            width=width, height=height
        )
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
        if self.reverse_button.isChecked():
            reverse_placement(placement, guide.length())
        self._placement = placement
        self._guide = guide
        self._clear_preview()
        for frame in placement.frames:
            self._frames_band.addGeometry(frame.geometry, self._crs)
        self._show_numbers(placement.frames)
        self._guide_band.setToGeometry(guide, self._crs)
        text += "\n" + self.tr("{count} sheets, overlap {overlap:.1f} %.").format(
            count=len(placement.frames), overlap=placement.overlap_pct
        )
        if placement.uncovered:
            text += "\n" + self.tr(
                "Part of the line is outside the sheets: use a larger sheet or scale."
            )
        self.info_label.setText(text)
        self.create_button.setEnabled(True)

    # ---- create -------------------------------------------------------------------------

    def _create(self):
        if self._placement is None:
            return
        name = self.name_edit.text().strip() or self.tr("Strip map")
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr("Save series"), f"{name}.gpkg", "GeoPackage (*.gpkg)"
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
            create_series(Path(path), self._crs, setup, self._placement)
            add_to_project(path, QgsProject.instance())
        except SeriesError as error:
            self._message(str(error))
            return
        self._reset(deselect=False)
        self.info_label.setText(self.tr("Saved {file}.").format(file=Path(path).name))

    def _reset(self, *_, deselect=True):
        """Forget the guide line and the preview so a new series can be started."""
        self._timer.stop()
        if self.canvas.mapTool() is self._draw_tool:
            self._stop_drawing()
        self._clear_preview()
        self._source = None
        self._from_selection = False
        self._guide = None
        self._placement = None
        self.create_button.setEnabled(False)
        self.guide_label.setText(self.tr("No guide line yet."))
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

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if self._source is not None:
            self._schedule_preview()  # the preview was cleared when the panel was closed

    def closeEvent(self, event):  # noqa: N802
        self._clear_preview()
        super().closeEvent(event)
