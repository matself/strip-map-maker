# SPDX-License-Identifier: GPL-2.0-or-later
"""Build dist/<module>-<version>.zip, ready for 'Install from ZIP' or plugins.qgis.org."""
import configparser
import zipfile
from pathlib import Path

MODULE = "strip_map_maker"
ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / MODULE
SKIP_DIRS = {"__pycache__", ".git"}
SKIP_SUFFIXES = {".pyc", ".ts"}


def read_version():
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(PLUGIN_DIR / "metadata.txt", encoding="utf-8")
    return parser["general"]["version"]


def main():
    out_dir = ROOT / "dist"
    out_dir.mkdir(exist_ok=True)
    target = out_dir / f"{MODULE}-{read_version()}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(PLUGIN_DIR.rglob("*")):
            rel = path.relative_to(ROOT)
            if path.is_dir() or SKIP_DIRS & set(rel.parts) or path.suffix in SKIP_SUFFIXES:
                continue
            zf.write(path, rel.as_posix())
        license_file = ROOT / "LICENSE"
        if license_file.exists():
            zf.write(license_file, f"{MODULE}/LICENSE")
    print(f"Wrote {target}")


if __name__ == "__main__":
    main()
