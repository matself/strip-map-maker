"""Load the plugin inside a real QGIS (skipped when QGIS is not importable)."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("qgis.core")

from qgis.testing import start_app  # noqa: E402
from qgis.testing.mocked import get_iface  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

start_app()


def test_class_factory_and_lifecycle():
    from strip_map_maker import classFactory

    plugin = classFactory(get_iface())
    plugin.initGui()
    plugin.unload()
