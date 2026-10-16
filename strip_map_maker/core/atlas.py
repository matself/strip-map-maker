# SPDX-License-Identifier: GPL-2.0-or-later
"""Build a print layout with an atlas from a saved series.

The series itself knows the sheet size and the scale (``series_info``), so the layout is
made without asking for them again. One map item has exactly the size of a sheet, a fixed
scale and is turned by the ``rotation`` field of the sheet. Below it there is a band with
the sheet number, the scale, a scale bar, a north arrow and an overview map that marks
the sheet being shown.

All sizes in this module are millimetres on paper.
"""
import math
from dataclasses import dataclass
from pathlib import Path

from qgis.core import (
    Qgis,
    QgsLayoutItem,
    QgsLayoutItemLabel,
    QgsLayoutItemMap,
    QgsLayoutItemMapOverview,
    QgsLayoutItemPage,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutObject,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsMapLayerStyle,
    QgsPrintLayout,
    QgsProject,
    QgsProperty,
    QgsTextFormat,
)
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QFont

from .series import LAYOUT_ROTATION_EXPRESSION, ROLE_PROPERTY, Setup

PAGES = {"A4": (297.0, 210.0), "A3": (420.0, 297.0)}  # landscape, width x height
TOP_MARGIN = 8.0
BOTTOM_MARGIN = 8.0
MIN_SIDE_MARGIN = 5.0
GAP = 3.0  # between the map and the band below it
MIN_BAND = 14.0  # least height of the band for anything useful
ARROW_EXTENT = 18.0  # most room (mm) the north arrow may take when turned
ARROW_GROWTH = 1.45  # a turned arrow's frame grows to at most this times its resting size
# A ring with an arrow: being circular, it keeps its apparent size when turned.
NORTH_ARROW = str(Path(__file__).resolve().parent.parent / "north_arrow.svg")
UNIT = Qgis.LayoutUnit.Millimeters


class AtlasError(RuntimeError):
    """The atlas cannot be built for this series and page."""


@dataclass
class AtlasOptions:
    page: str = "A4"
    overview: bool = True
    scale_bar: bool = True
    north_arrow: bool = True
    name: str = ""


def page_band(page: str, setup: Setup) -> float:
    """Height (mm) left for the band under the map; raises ``AtlasError`` if it won't fit."""
    if page not in PAGES:
        raise AtlasError(
            QCoreApplication.translate("Atlas", "Unknown page size '{page}'.").format(page=page)
        )
    width, height = PAGES[page]
    if setup.width_mm > width - 2 * MIN_SIDE_MARGIN:
        raise AtlasError(
            QCoreApplication.translate(
                "Atlas",
                "A sheet {sheet:g} mm wide does not fit on {page} landscape "
                "(room for {room:g} mm). Use a larger page or a narrower sheet.",
            ).format(sheet=setup.width_mm, page=page, room=width - 2 * MIN_SIDE_MARGIN)
        )
    if setup.height_mm > height - TOP_MARGIN - BOTTOM_MARGIN:
        raise AtlasError(
            QCoreApplication.translate(
                "Atlas",
                "A sheet {sheet:g} mm high does not fit on {page} landscape "
                "(room for {room:g} mm). Use a larger page or a lower sheet.",
            ).format(
                sheet=setup.height_mm, page=page, room=height - TOP_MARGIN - BOTTOM_MARGIN
            )
        )
    return height - TOP_MARGIN - setup.height_mm - GAP - BOTTOM_MARGIN


def _nice_length(target: float) -> float:
    """A round number (1, 2, 5 times a power of ten) not above ``target``."""
    if target <= 0:
        return 1.0
    power = 10 ** math.floor(math.log10(target))
    for step in (5, 2, 1):
        if step * power <= target:
            return step * power
    return power


def _unique_name(manager, name: str) -> str:
    if manager.layoutByName(name) is None:
        return name
    number = 2
    while manager.layoutByName(f"{name} {number}") is not None:
        number += 1
    return f"{name} {number}"


def _rotation_property(frames_layer):
    if frames_layer.fields().indexFromName("rotation") >= 0:
        return QgsProperty.fromField("rotation")
    return QgsProperty.fromExpression(LAYOUT_ROTATION_EXPRESSION)  # a series without the field


def _place(item, x, y, width, height):
    item.attemptMove(QgsLayoutPoint(x, y, UNIT))
    item.attemptResize(QgsLayoutSize(width, height, UNIT))


def _label(layout, text, x, y, width, height, size, bold=False):
    label = QgsLayoutItemLabel(layout)
    label.setText(text)
    font = QFont("Arial")
    font.setBold(bold)
    text_format = QgsTextFormat()
    text_format.setFont(font)
    text_format.setSize(size)
    text_format.setSizeUnit(Qgis.RenderUnit.Points)
    label.setTextFormat(text_format)
    layout.addLayoutItem(label)
    _place(label, x, y, width, height)
    return label


def _small_numbers_style(layer, size_pt=7.0):
    """Style of the sheets layer with small numbers, for the overview map only."""
    original = layer.labeling().clone()
    small = original.clone()
    settings = small.settings()
    text = settings.format()
    text.setSize(size_pt)
    settings.setFormat(text)
    small.setSettings(settings)
    layer.setLabeling(small)
    style = QgsMapLayerStyle()
    style.readFromLayer(layer)
    layer.setLabeling(original)
    return style.xmlData()


def visible_layers(project: QgsProject):
    """The checked layers in drawing order, series sheet layers left out."""
    root = project.layerTreeRoot()
    checked = {layer.id() for layer in root.checkedLayers()}
    layers = [layer for layer in root.layerOrder() if layer.id() in checked]
    return [layer for layer in layers if layer.customProperty(ROLE_PROPERTY) != "frames"]


def build_atlas(project: QgsProject, frames_layer, setup: Setup, options: AtlasOptions):
    """Create the layout in ``project`` and return it. Raises ``AtlasError`` if it won't fit."""
    band = page_band(options.page, setup)
    page_width, page_height = PAGES[options.page]
    manager = project.layoutManager()
    default_name = QCoreApplication.translate("Atlas", "{name} atlas").format(name=setup.name)
    name = _unique_name(manager, options.name or default_name)

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName(name)
    page = layout.pageCollection().page(0)
    page.setPageSize(options.page, QgsLayoutItemPage.Orientation.Landscape)

    left = (page_width - setup.width_mm) / 2
    layers = visible_layers(project)

    main = QgsLayoutItemMap(layout)
    layout.addLayoutItem(main)
    _place(main, left, TOP_MARGIN, setup.width_mm, setup.height_mm)
    main.setFrameEnabled(True)
    main.setLayers(layers)
    main.setCrs(frames_layer.crs())
    main.zoomToExtent(frames_layer.extent())
    main.setScale(setup.scale)
    main.setAtlasDriven(True)
    main.setAtlasScalingMode(QgsLayoutItemMap.AtlasScalingMode.Fixed)
    main.dataDefinedProperties().setProperty(
        QgsLayoutObject.DataDefinedProperty.MapRotation, _rotation_property(frames_layer)
    )

    atlas = layout.atlas()
    atlas.setCoverageLayer(frames_layer)
    atlas.setEnabled(True)
    atlas.setSortFeatures(True)
    atlas.setSortExpression('"id"')
    atlas.setPageNameExpression('"id"')

    if band >= MIN_BAND:
        top = TOP_MARGIN + setup.height_mm + GAP
        _label(
            layout,
            QCoreApplication.translate(
                "Atlas", "Sheet [% @atlas_featurenumber %] of [% @atlas_totalfeatures %]"
            ),
            left, top, 60, 7, 13, bold=True,
        )
        scale_text = QCoreApplication.translate("Atlas", "Scale 1:{scale}").format(
            scale=f"{setup.scale:,}".replace(",", " ")
        )
        _label(layout, scale_text, left, top + 8, 60, 6, 10)
        x = left + 62
        if options.scale_bar:
            bar = QgsLayoutItemScaleBar(layout)
            bar.setLinkedMap(main)
            bar.setStyle("Line Ticks Middle")
            bar.setUnits(Qgis.DistanceUnit.Meters)
            bar.setUnitLabel("m")
            bar.setNumberOfSegmentsLeft(0)
            bar.setNumberOfSegments(2)
            ground_width = setup.width_mm * setup.scale / 1000
            bar.setUnitsPerSegment(_nice_length(ground_width / 10))
            layout.addLayoutItem(bar)
            bar.attemptMove(QgsLayoutPoint(x, top, UNIT))
            x += 62
        if options.north_arrow:
            arrow = QgsLayoutItemPicture(layout)
            arrow.setPicturePath(NORTH_ARROW)
            arrow.setLinkedMap(main)
            arrow.setNorthMode(QgsLayoutItemPicture.NorthMode.GridNorth)
            layout.addLayoutItem(arrow)
            # QGIS shrinks a turned picture to fit its box, so the arrow would change size
            # with the rotation. ZoomResizeFrame lets the frame follow the turned arrow
            # instead; the frame is anchored at its middle and given room to grow.
            extent = min(band, ARROW_EXTENT)
            side = extent / ARROW_GROWTH
            arrow.setResizeMode(QgsLayoutItemPicture.ResizeMode.ZoomResizeFrame)
            arrow.setReferencePoint(QgsLayoutItem.ReferencePoint.Middle)
            arrow.attemptResize(QgsLayoutSize(side, side, UNIT))
            arrow.attemptMove(QgsLayoutPoint(x + extent / 2, top + extent / 2, UNIT))
            x += extent + 2
        right = left + setup.width_mm
        if options.overview and right - x >= 30:
            overview = QgsLayoutItemMap(layout)
            layout.addLayoutItem(overview)
            _place(overview, x, top, right - x, band)
            overview.setFrameEnabled(True)
            overview.setLayers([frames_layer] + layers)
            overview.setKeepLayerStyles(True)
            overview.setLayerStyleOverrides({frames_layer.id(): _small_numbers_style(frames_layer)})
            overview.setCrs(frames_layer.crs())
            overview.zoomToExtent(frames_layer.extent())
            marker = QgsLayoutItemMapOverview("Sheet", overview)
            marker.setLinkedMap(main)
            overview.overviews().addOverview(marker)

    manager.addLayout(layout)
    return layout
