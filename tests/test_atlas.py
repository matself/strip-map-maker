# SPDX-License-Identifier: GPL-2.0-or-later
"""Layout and atlas built from a saved series (need qgis.core, no GUI)."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("qgis.core")

from qgis.core import (  # noqa: E402
    QgsCoordinateReferenceSystem,
    QgsGeometry,
    QgsLayoutItemMap,
    QgsPointXY,
    QgsProject,
)
from qgis.testing import start_app  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strip_map_maker.core.atlas import (  # noqa: E402
    AtlasError,
    AtlasOptions,
    build_atlas,
    page_band,
)
from strip_map_maker.core.placement import place_frames  # noqa: E402
from strip_map_maker.core.series import (  # noqa: E402
    Setup,
    add_to_project,
    create_series,
    read_setup,
)

start_app()


def _series(tmp_path, width_mm, height_mm):
    crs = QgsCoordinateReferenceSystem("EPSG:3006")
    guide = QgsGeometry.fromPolylineXY(
        [QgsPointXY(500000, 6500000), QgsPointXY(500800, 6500300), QgsPointXY(501600, 6500200)]
    )
    setup = Setup("Route", width_mm=width_mm, height_mm=height_mm, scale=1000, overlap_pct=10)
    placement = place_frames(guide, setup.width_m, setup.height_m, setup.overlap_pct)
    path = tmp_path / "route.gpkg"
    create_series(path, crs, setup, placement)
    return path, setup, len(placement.frames)


def test_page_band_and_fit_errors():
    setup = Setup("x", width_mm=277, height_mm=150, scale=1000, overlap_pct=10)
    assert page_band("A4", setup) == pytest.approx(210 - 8 - 150 - 3 - 8)
    with pytest.raises(AtlasError):
        page_band("A4", Setup("x", 297, 150, 1000, 10))  # too wide
    with pytest.raises(AtlasError):
        page_band("A4", Setup("x", 277, 200, 1000, 10))  # too high
    assert page_band("A3", Setup("x", 297, 210, 1000, 10)) > 0


def test_build_atlas_from_series(tmp_path):
    path, setup, count = _series(tmp_path, 277, 150)
    project = QgsProject()
    frames = add_to_project(path, project)
    layout = build_atlas(project, frames, read_setup(path), AtlasOptions(page="A4"))

    assert project.layoutManager().layoutByName(layout.name()) is layout
    atlas = layout.atlas()
    assert atlas.enabled() and atlas.coverageLayer() is frames
    maps = [item for item in layout.items() if isinstance(item, QgsLayoutItemMap)]
    main = next(item for item in maps if item.atlasDriven())
    assert main.sizeWithUnits().width() == pytest.approx(277)
    assert main.sizeWithUnits().height() == pytest.approx(150)
    assert main.scale() == pytest.approx(1000)
    assert len(maps) == 2  # the main map and the overview
    assert atlas.updateFeatures() == count

    again = build_atlas(project, frames, read_setup(path), AtlasOptions(page="A3"))
    assert again.name() != layout.name()


def test_sheet_too_big_for_page(tmp_path):
    path, setup, _ = _series(tmp_path, 297, 210)
    project = QgsProject()
    frames = add_to_project(path, project)
    with pytest.raises(AtlasError):
        build_atlas(project, frames, read_setup(path), AtlasOptions(page="A4"))
