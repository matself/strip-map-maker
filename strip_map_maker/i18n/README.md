# Translations

Extract strings and compile translations (Qt Linguist tools required):

```bash
pylupdate5 plugin.py provider.py algorithms/example_algorithm.py -ts i18n/strip_map_maker_sv.ts
lrelease i18n/strip_map_maker_sv.ts
```

`plugin.py` loads `i18n/strip_map_maker_<locale>.qm` automatically.
Source strings stay in English.
