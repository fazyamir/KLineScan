#!/usr/bin/env python3
"""Build a normalized SQLite catalog from JSON diagnostic packs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from klinescan.core.catalog import CatalogDatabase


def build(pack_dir: Path, output: Path) -> int:
    catalog = CatalogDatabase(output)
    count = 0
    for path in sorted(pack_dir.glob("*.json")):
        count += catalog.import_pack(json.loads(path.read_text(encoding="utf-8")), path.name)
    catalog.close()
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packs", type=Path, default=Path("packs"))
    parser.add_argument("--output", type=Path, default=Path("catalog.sqlite3"))
    args = parser.parse_args()
    print(f"Imported {build(args.packs, args.output)} vehicle variants into {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
