# SPDX-License-Identifier: GPL-2.0-or-later
"""A map series on disk: one GeoPackage holding the guide line, the frames and the setup.

Layers in the GeoPackage:
  guide        exactly one line feature
  frames       one polygon per frame (id, azi, x, y, from_m, to_m)
  series_info  key/value table with the setup the frames were built for
"""
from dataclasses import asdict, dataclass
from pathlib import Path

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransformContext,
    QgsFeature,
    QgsField,
    QgsFillSymbol,
    QgsGeometry,
    QgsPalLayerSettings,
    QgsProject,
    QgsProperty,
    QgsSimpleFillSymbolLayer,
    QgsSingleSymbolRenderer,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtCore import QMetaType, QVariant
from qgis.PyQt.QtGui import QColor, QFont

from .placement import Placement

GUIDE_LAYER = "guide"
FRAMES_LAYER = "frames"
INFO_LAYER = "series_info"
ROLE_PROPERTY = "strip_map_maker/role"


class SeriesError(RuntimeError):
    """The series could not be written or read."""


@dataclass
class Setup:
    """The parameters a series was built for. Fixed once the frames are accepted."""

    name: str
    width_mm: float
    height_mm: float
    scale: int
    overlap_pct: float
    smoothing_m: float = 0.0

    @property
    def width_m(self) -> float:
        return self.width_mm * self.scale / 1000.0

    @property
    def height_m(self) -> float:
        return self.height_mm * self.scale / 1000.0


def _field(name, kind):
    """A QgsField that works on both the old (QVariant) and new (QMetaType) API."""
    try:
        return QgsField(name, getattr(QMetaType.Type, kind))
    except AttributeError:
        return QgsField(name, getattr(QVariant, kind))


def _write(layer, path, layer_name, first):
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = layer_name
    try:
        action = QgsVectorFileWriter.ActionOnExistingFile
        options.actionOnExistingFile = (
            action.CreateOrOverwriteFile if first else action.CreateOrOverwriteLayer
        )
    except AttributeError:
        options.actionOnExistingFile = (
            QgsVectorFileWriter.CreateOrOverwriteFile
            if first
            else QgsVectorFileWriter.CreateOrOverwriteLayer
        )
    result = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer, str(path), QgsCoordinateTransformContext(), options
    )
    if result[0] != QgsVectorFileWriter.WriterError.NoError:
        raise SeriesError(f"Could not write layer '{layer_name}': {result[1]}")


def create_series(
    path, guide: QgsGeometry, crs: QgsCoordinateReferenceSystem, setup: Setup, placement: Placement
):
    """Write a new series to ``path`` (an existing file is replaced)."""
    path = Path(path)
    uri = f"LineString?crs={crs.authid() or crs.toWkt()}"

    guide_layer = QgsVectorLayer(uri, GUIDE_LAYER, "memory")
    guide_layer.dataProvider().addAttributes([_field("name", "QString")])
    guide_layer.updateFields()
    feature = QgsFeature(guide_layer.fields())
    feature["name"] = setup.name
    feature.setGeometry(guide)
    guide_layer.dataProvider().addFeature(feature)

    frames_layer = QgsVectorLayer(uri.replace("LineString", "Polygon"), FRAMES_LAYER, "memory")
    frames_layer.dataProvider().addAttributes(
        [
            _field("id", "Int"),
            _field("azi", "Double"),
            _field("x", "Double"),
            _field("y", "Double"),
            _field("from_m", "Double"),
            _field("to_m", "Double"),
        ]
    )
    frames_layer.updateFields()
    for frame in placement.frames:
        feature = QgsFeature(frames_layer.fields())
        feature.setAttributes([frame.id, frame.azi, frame.x, frame.y, frame.from_m, frame.to_m])
        feature.setGeometry(frame.geometry)
        frames_layer.dataProvider().addFeature(feature)

    info = {
        **{k: str(v) for k, v in asdict(setup).items()},
        "spacing_m": str(placement.spacing),
        "achieved_overlap_pct": str(placement.overlap_pct),
        "frame_count": str(len(placement.frames)),
    }
    info_layer = QgsVectorLayer("None", INFO_LAYER, "memory")
    info_layer.dataProvider().addAttributes([_field("key", "QString"), _field("value", "QString")])
    info_layer.updateFields()
    for key, value in info.items():
        feature = QgsFeature(info_layer.fields())
        feature.setAttributes([key, value])
        info_layer.dataProvider().addFeature(feature)

    _write(guide_layer, path, GUIDE_LAYER, first=True)
    _write(frames_layer, path, FRAMES_LAYER, first=False)
    _write(info_layer, path, INFO_LAYER, first=False)


def _open(path, name):
    layer = QgsVectorLayer(f"{path}|layername={name}", name, "ogr")
    if not layer.isValid():
        raise SeriesError(f"'{name}' not found in {path}")
    return layer


def read_setup(path) -> Setup:
    """Read the setup a series was built for."""
    values = {f["key"]: f["value"] for f in _open(path, INFO_LAYER).getFeatures()}
    try:
        return Setup(
            name=values["name"],
            width_mm=float(values["width_mm"]),
            height_mm=float(values["height_mm"]),
            scale=int(float(values["scale"])),
            overlap_pct=float(values["overlap_pct"]),
            smoothing_m=float(values.get("smoothing_m", 0)),
        )
    except KeyError as missing:
        raise SeriesError(f"Series setup is incomplete: {missing} missing") from None


def apply_default_style(layer: QgsVectorLayer):
    """Transparent fill, dark outline, and the frame number rotated along the long edge."""
    symbol_layer = QgsSimpleFillSymbolLayer.create(
        {
            "color": "190,178,151,0",
            "outline_color": "35,35,35,255",
            "outline_width": "0.46",
            "outline_width_unit": "MM",
            "style": "solid",
            "joinstyle": "bevel",
        }
    )
    symbol = QgsFillSymbol()
    symbol.changeSymbolLayer(0, symbol_layer)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))

    settings = QgsPalLayerSettings()
    settings.fieldName = "id"
    settings.placement = Qgis.LabelPlacement.OverPoint
    settings.dataDefinedProperties().setProperty(
        QgsPalLayerSettings.Property.LabelRotation,
        QgsProperty.fromExpression('("azi" - 90 + 360) % 360'),
    )
    text = QgsTextFormat()
    font = QFont("Arial", 18)
    font.setBold(True)
    text.setFont(font)
    text.setSize(18)
    text.setSizeUnit(Qgis.RenderUnit.Points)
    text.setColor(QColor(0, 0, 0))
    buffer = QgsTextBufferSettings()
    buffer.setEnabled(True)
    buffer.setSize(1)
    buffer.setSizeUnit(Qgis.RenderUnit.Millimeters)
    buffer.setColor(QColor(255, 255, 255))
    text.setBuffer(buffer)
    settings.setFormat(text)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)


def add_to_project(path, project: QgsProject = None):
    """Load a series into ``project`` as a layer group; returns (guide, frames) layers."""
    project = project or QgsProject.instance()
    setup = read_setup(path)
    guide, frames = _open(path, GUIDE_LAYER), _open(path, FRAMES_LAYER)
    guide.setName(f"{setup.name} - guide")
    frames.setName(f"{setup.name} - frames")
    guide.setCustomProperty(ROLE_PROPERTY, "guide")
    frames.setCustomProperty(ROLE_PROPERTY, "frames")
    apply_default_style(frames)
    project.addMapLayer(guide, False)
    project.addMapLayer(frames, False)
    group = project.layerTreeRoot().addGroup(f"{setup.name} (1:{setup.scale})")
    group.addLayer(frames)
    group.addLayer(guide)
    return guide, frames
