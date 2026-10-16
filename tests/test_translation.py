# SPDX-License-Identifier: GPL-2.0-or-later
"""The Swedish translation: complete, loadable, and applied to the panel and to messages."""
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

pytest.importorskip("qgis.gui")

from qgis.core import QgsCoordinateReferenceSystem, QgsGeometry, QgsPointXY  # noqa: E402
from qgis.gui import QgsMapCanvas  # noqa: E402
from qgis.PyQt.QtCore import QCoreApplication, QTranslator  # noqa: E402
from qgis.testing import start_app  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "strip_map_maker"
sys.path.insert(0, str(PLUGIN_DIR.parent))

start_app()


class FakeIface:
    def __init__(self):
        self.canvas = QgsMapCanvas()

    def mapCanvas(self):  # noqa: N802
        return self.canvas

    def messageBar(self):  # noqa: N802
        return self

    def pushMessage(self, *args, **kwargs):  # noqa: N802
        pass


@pytest.fixture
def swedish():
    translator = QTranslator()
    assert translator.load(str(PLUGIN_DIR / "i18n" / "strip_map_maker_sv.qm"))
    QCoreApplication.installTranslator(translator)
    yield
    QCoreApplication.removeTranslator(translator)


def test_every_message_is_translated_and_keeps_its_placeholders():
    root = ET.parse(PLUGIN_DIR / "i18n" / "strip_map_maker_sv.ts").getroot()
    messages = list(root.iter("message"))
    assert len(messages) > 50
    for message in messages:
        source = message.findtext("source")
        translation = message.find("translation")
        assert translation.get("type") is None, f"unfinished: {source}"
        assert translation.text, f"empty: {source}"
        assert sorted(re.findall(r"\{[^}]*\}", source)) == sorted(
            re.findall(r"\{[^}]*\}", translation.text)
        ), f"placeholders differ: {source}"


def test_panel_is_swedish_when_the_translation_is_loaded(swedish):
    from strip_map_maker.dockwidget import StripMapMakerDockWidget

    dock = StripMapMakerDockWidget(FakeIface())
    assert dock.create_button.text() == "Spara serie..."
    assert dock.atlas_button.text() == "Skapa atlas"
    assert dock.guide_label.text() == "Ingen guidelinje än."
    assert dock.name_edit.text() == "kartserie"  # the default series name
    assert dock.preset_combo.itemText(1) == "A4 liggande"
    assert dock.preset_combo.itemData(1) == "A4 landscape"  # the key stays English
    guide = QgsGeometry.fromPolylineXY([QgsPointXY(0, 0), QgsPointXY(2000, 0)])
    dock._set_guide(guide, QgsCoordinateReferenceSystem("EPSG:3006"))
    dock._update_preview()
    assert "blad, överlapp" in dock.info_label.text()
    dock.cleanup()


def test_core_messages_follow_the_language(swedish):
    from strip_map_maker.core.guide import GuideError, chain_lines

    with pytest.raises(GuideError, match="Det finns ingen linjegeometri"):
        chain_lines([])


def test_english_is_the_default():
    from strip_map_maker.core.guide import GuideError, chain_lines

    with pytest.raises(GuideError, match="No line geometry"):
        chain_lines([])
