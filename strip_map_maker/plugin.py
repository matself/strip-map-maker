"""Main plugin class for Strip Map Maker."""
from pathlib import Path

from qgis.PyQt.QtCore import QCoreApplication, QSettings, Qt, QTranslator
from qgis.PyQt.QtGui import QIcon

from .compat import QAction
from .dockwidget import StripMapMakerDockWidget

PLUGIN_NAME = "Strip Map Maker"


class StripMapMakerPlugin:
    """QGIS plugin implementation."""

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = Path(__file__).parent
        self.action = None
        self.dock = None
        self.translator = None
        self._load_translation()

    def _load_translation(self):
        locale = QSettings().value("locale/userLocale", "en")[:2]
        path = self.plugin_dir / "i18n" / f"strip_map_maker_{locale}.qm"
        if path.exists():
            self.translator = QTranslator()
            self.translator.load(str(path))
            QCoreApplication.installTranslator(self.translator)

    @staticmethod
    def tr(message):
        return QCoreApplication.translate("StripMapMakerPlugin", message)

    def initGui(self):  # noqa: N802 (name required by QGIS)
        icon = QIcon(str(self.plugin_dir / "icon.svg"))
        self.action = QAction(icon, self.tr(PLUGIN_NAME), self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&" + PLUGIN_NAME, self.action)

    def unload(self):
        self.iface.removePluginMenu("&" + PLUGIN_NAME, self.action)
        self.iface.removeToolBarIcon(self.action)
        self.action.deleteLater()
        self.action = None
        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        if self.translator is not None:
            QCoreApplication.removeTranslator(self.translator)
            self.translator = None

    def run(self):
        if self.dock is None:
            self.dock = StripMapMakerDockWidget(self.iface.mainWindow())
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        self.dock.setVisible(not self.dock.isVisible())
