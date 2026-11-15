"""Command line for decoding user-supplied diagnostic container files.

Usage::

    python -m klinescan.containers decode FILE... -o OUTDIR [--extract DIR]

``decode`` writes one detailed JSON report per file plus ``all_brands.json``.
Unknown layouts are reported, never guessed.  No container image is bundled.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from klinescan.containers.export import decode_file, merge_brands


def _inputs(paths: list[Path]) -> list[Path]:
    files = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(p for p in path.iterdir()
                                if p.is_file() and p.suffix.lower().lstrip(".") == "dat"))
        elif path.suffix.lower().lstrip(".") != "eep":
            files.append(path)
    return files


def _count(report: dict) -> int:
    cat = report.get("catalog") or {}
    if "record_count" in cat:
        return cat["record_count"]
    return (sum(len(s["records"]) for s in cat.get("sections", []))
            + len(cat.get("campaigns", []))
            + sum(len(m["campaigns"]) for m in cat.get("menus", [])))


def cmd_decode(args) -> int:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    reports = []
    for path in _inputs(args.inputs):
        report = decode_file(path, args.extract)
        (args.output_dir / f"{path.name}.json").write_text(
            json.dumps(report, indent=1) + "\n", encoding="utf-8")
        extra = f" {report['stats']}" if "stats" in report else ""
        print(f"{path.name}: {report['container']}/{report['family']} records={_count(report)}{extra}")
        reports.append(report)
    combined = merge_brands(reports)
    (args.output_dir / "all_brands.json").write_text(
        json.dumps(combined, indent=1) + "\n", encoding="utf-8")
    print(f"all_brands.json: {combined['stats']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m klinescan.containers", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("decode", help="detailed JSON per file + all_brands.json")
    p.add_argument("inputs", nargs="+", type=Path)
    p.add_argument("-o", "--output-dir", type=Path, required=True)
    p.add_argument("--extract", type=Path, help="write decompressed modules/bitmaps here")
    p.set_defaults(func=cmd_decode)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
