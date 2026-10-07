"""Strip Map Maker - QGIS plugin entry point."""


def classFactory(iface):  # noqa: N802 (name required by QGIS)
    """Load the plugin class. Called by QGIS."""
    from .plugin import StripMapMakerPlugin

    return StripMapMakerPlugin(iface)
