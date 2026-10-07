# SPDX-License-Identifier: GPL-2.0-or-later
"""Smoke test: build the dock on a real canvas and run a preview."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("qgis.gui")

from qgis.core import QgsCoordinateReferenceSystem, QgsGeometry, QgsPointXY  # noqa: E402
from qgis.gui import QgsMapCanvas  # noqa: E402
from qgis.testing import start_app  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

start_app()


class FakeIface:
    def __init__(self):
        self.canvas = QgsMapCanvas()

    def mapCanvas(self):  # noqa: N802
        return self.canvas

    def messageBar(self):  # noqa: N802
        return self

    def pushMessage(self, *args, **kwargs):  # noqa: N802
        self.last_message = args


def test_dock_preview_for_a_guide_line():
    from strip_map_maker.dockwidget import StripMapMakerDockWidget

    dock = StripMapMakerDockWidget(FakeIface())
    guide = QgsGeometry.fromPolylineXY([QgsPointXY(0, 0), QgsPointXY(2000, 0)])
    dock._set_guide(guide, QgsCoordinateReferenceSystem("EPSG:3006"))
    dock._update_preview()  # normally fired by the debounce timer
    assert dock._placement is not None
    assert len(dock._placement.frames) == 8  # 280 mm at 1:1000 = 280 m, 10 % overlap
    assert dock.create_button.isEnabled()
    dock._reset()
    assert dock._source is None and dock._placement is None
    assert not dock.create_button.isEnabled()
    dock.cleanup()
