# Translations

Source strings are English. Each language is a Qt Linguist file `strip_map_maker_<code>.ts`
compiled to `strip_map_maker_<code>.qm`; `plugin.py` loads the `.qm` that matches the QGIS
language automatically. Both the `.ts` source and the compiled `.qm` are kept in the
repository (CI and the zip need the `.qm`); only the `.qm` goes into the plugin zip. Recompile
and commit the `.qm` whenever the `.ts` changes.

Strings are marked with `self.tr()` in the panel and `QCoreApplication.translate()` in
`core/` (contexts `Guide`, `Placement`, `Atlas`, `Series`). Texts with values use named
placeholders such as `{count}`; keep them unchanged in the translation.

Update the `.ts` file after changing strings, translate the new entries, then compile
(`lupdate` and `lrelease` come with Qt, or with `pip install pyside6-essentials`):

```bash
lupdate strip_map_maker/dockwidget.py strip_map_maker/plugin.py strip_map_maker/maptool.py \
  strip_map_maker/core/guide.py strip_map_maker/core/placement.py strip_map_maker/core/atlas.py \
  strip_map_maker/core/series.py -ts strip_map_maker/i18n/strip_map_maker_sv.ts
lrelease strip_map_maker/i18n/strip_map_maker_sv.ts -qm strip_map_maker/i18n/strip_map_maker_sv.qm
```

`tests/test_translation.py` checks that no entry is unfinished and that the placeholders match.
