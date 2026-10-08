# SPDX-License-Identifier: GPL-2.0-or-later
"""One round trip through a GeoPackage (need qgis.core, no GUI)."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("qgis.core")

from qgis.core import (  # noqa: E402
    QgsCoordinateReferenceSystem,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
)
from qgis.testing import start_app  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strip_map_maker.core.placement import place_frames  # noqa: E402
from strip_map_maker.core.series import (  # noqa: E402
    Setup,
    add_to_project,
    create_series,
    read_setup,
)

start_app()


def test_create_and_reload_series(tmp_path):
    crs = QgsCoordinateReferenceSystem("EPSG:3006")
    guide = QgsGeometry.fromPolylineXY([QgsPointXY(500000, 6500000), QgsPointXY(501000, 6500000)])
    setup = Setup("Route A", width_mm=280, height_mm=180, scale=500, overlap_pct=10)
    assert setup.width_m == pytest.approx(140)
    placement = place_frames(guide, setup.width_m, setup.height_m, setup.overlap_pct)

    path = tmp_path / "route_a.gpkg"
    create_series(path, crs, setup, placement)

    assert read_setup(path) == setup
    project = QgsProject()
    frames_layer = add_to_project(path, project)
    assert frames_layer.featureCount() == len(placement.frames)
    assert frames_layer.crs().authid() == "EPSG:3006"
    first = next(frames_layer.getFeatures(), None)
    assert first["id"] == 1 and first["from_m"] == pytest.approx(0)
    assert -90 <= first["rotation"] < 90


def test_layout_rotation_expression_matches_azi(tmp_path):
    from qgis.core import QgsExpression, QgsExpressionContext, QgsExpressionContextUtils

    from strip_map_maker.core.series import LAYOUT_ROTATION_EXPRESSION, layout_rotation

    crs = QgsCoordinateReferenceSystem("EPSG:3006")
    guide = QgsGeometry.fromPolylineXY(
        [QgsPointXY(500000, 6500000), QgsPointXY(500600, 6500400), QgsPointXY(501200, 6500500)]
    )
    setup = Setup("Bend", width_mm=280, height_mm=180, scale=1000, overlap_pct=10)
    placement = place_frames(guide, setup.width_m, setup.height_m, setup.overlap_pct)
    path = tmp_path / "bend.gpkg"
    create_series(path, crs, setup, placement)
    project = QgsProject()
    frames_layer = add_to_project(path, project)
    expression = QgsExpression(LAYOUT_ROTATION_EXPRESSION)
    for feature in frames_layer.getFeatures():
        context = QgsExpressionContext()
        context.appendScope(QgsExpressionContextUtils.layerScope(frames_layer))
        context.setFeature(feature)
        assert expression.evaluate(context) == pytest.approx(feature["rotation"], abs=1e-6)
        assert feature["rotation"] == pytest.approx(layout_rotation(feature["azi"]), abs=1e-6)
