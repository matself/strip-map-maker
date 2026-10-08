# Strip Map Maker

Lays out a numbered series of rotated, overlapping map sheets along a line - a road, pipeline,
river or power line - for a chosen scale and sheet size. Use the result as a QGIS Atlas coverage
layer, or as a sheet index for planning, inventory and environmental surveys.

Compatible with QGIS 3.34 up to 4.99 (Qt5 and Qt6).

## Usage

1. Open the **Strip Map Maker** panel (toolbar icon or *Plugins* menu) and set the **Sheets**:
   width and height in mm, scale and overlap. The preview updates as you type.
2. **Guide line:** select one or more lines in a line layer (several lines are chained into
   one; ends up to *Join gaps up to* apart are bridged), or press *Draw line on map* (right click,
   double click, Enter or the *Finish line* button ends the line). The layer or project must use
   a projected CRS in metres. *Smoothing* (an averaging length in metres) flattens small meanders
   so the sheets follow the general direction of the line; the original line is still what has
   to fit inside the sheets. *Reverse sheet numbering* numbers the sheets from the other end.
3. **Save series...** saves a GeoPackage with two tables and adds the sheets to the project. The
   guide line itself is not saved:

   | Table | Content |
   |-------|---------|
   | `frames` | one polygon per sheet: `id`, `azi` (bearing, 0 = north, clockwise), `x`, `y` (centre), `from_m`, `to_m` (distance along the guide), `rotation` (map item rotation for the layout) |
   | `series_info` | the size, scale, overlap and smoothing the series was built for |

The first sheet starts at the start of the line and the last one ends at its end. Sheets are
spaced by their real overlap, so bends do not overlap more than straight stretches, and the overlap
is never smaller than requested (the last sheet may overlap more). Sheets that do not suit a
particular stretch can be moved or rotated with QGIS's own editing tools; the sheet label follows
the geometry.

## Using the sheets in a print layout

1. Add a map item with the same size as the sheet and set the scale.
2. *Atlas* panel: coverage layer = `frames`, sort by `id`. In the map item tick
   *Controlled by atlas* with margin 0 %.
3. Map item rotation, data-defined override: the `rotation` field of the sheet.

`rotation` turns the sheet so that its long edge runs horizontally and the sheet number reads
upright, as it does on the overview map: that is the sheet's "up". Rotation is between -90 and
90 degrees, so a route heading west is shown running from right to left, not upside down.
The field is a default value that is recalculated from the sheet geometry when a sheet is
turned or moved by hand.

To have the route run from left to right on every page, even when that turns the map upside
down, use this expression instead:

```
(90 - degrees(azimuth(point_n($geometry, 4), point_n($geometry, 1))) + 360) % 360
```

## Development

Link the plugin folder into your QGIS profile so changes are picked up (use the Plugin Reloader plugin):

```powershell
# Windows (run once, adjust profile name if needed)
New-Item -ItemType Junction `
  -Path "$env:APPDATA\QGIS\QGIS3\profiles\default\python\plugins\strip_map_maker" `
  -Target "$PWD\strip_map_maker"
```

Run the tests with a Python that has `qgis` and `pytest` (for example the QGIS OSGeo4W shell):

```bash
python -m pytest
```

Build an installable zip:

```bash
python scripts/build_zip.py   # -> dist/strip_map_maker-0.1.0.zip
```

### Qt5 / Qt6 compatibility rules

- Import Qt only via `qgis.PyQt`, never `PyQt5` / `PyQt6`.
- Use fully scoped enums (`Qt.DockWidgetArea.RightDockWidgetArea`).
- Use `exec()`, not `exec_()`.
- Put anything that differs between QGIS 3 and 4 in `strip_map_maker/compat.py`.
- plugins.qgis.org runs an automatic Qt6 check on upload (see the *Qt6 Check* tab).

## Background

The idea started as a console script, *Map Sheets Along Route*, in
[pyqgis-cartography](https://github.com/matself/pyqgis-cartography). The plugin reuses that
workflow and is relicensed by its author. Parts of the original script were written with help
from ChatGPT and Gemini.

## License

GPL-2.0-or-later - see [LICENSE](LICENSE).
