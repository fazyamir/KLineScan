#!/usr/bin/env python3
"""Bump the KLineScan version in ``klinescan/__version__.py`` and ``version.txt``."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = Path("klinescan") / "__version__.py"
TEXT_FILE = Path("version.txt")
VERSION_RE = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


def read_version(root: Path = ROOT) -> str:
    text = (root / VERSION_FILE).read_text(encoding="utf-8")
    match = VERSION_RE.search(text)
    if not match:
        raise ValueError(f"no __version__ assignment in {root / VERSION_FILE}")
    return match.group(1)


def bump_version(version: str, part: str = "patch") -> str:
    pieces = version.split(".")
    if len(pieces) != 3 or not all(piece.isdigit() for piece in pieces):
        raise ValueError(f"expected MAJOR.MINOR.PATCH, got {version!r}")
    major, minor, patch = (int(piece) for piece in pieces)
    if part == "major":
        major, minor, patch = major + 1, 0, 0
    elif part == "minor":
        minor, patch = minor + 1, 0
    elif part == "patch":
        patch += 1
    else:
        raise ValueError(f"unknown version part {part!r}")
    return f"{major}.{minor}.{patch}"


def write_version(version: str, root: Path = ROOT) -> None:
    path = root / VERSION_FILE
    text = path.read_text(encoding="utf-8")
    updated = VERSION_RE.sub(f'__version__ = "{version}"', text, count=1)
    if updated == text:
        raise ValueError(f"could not update {path}")
    path.write_text(updated, encoding="utf-8")
    (root / TEXT_FILE).write_text(f"{version}\n", encoding="utf-8")


def update_version(root: Path = ROOT, part: str = "patch") -> str:
    version = bump_version(read_version(root), part)
    write_version(version, root)
    return version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--part", choices=("major", "minor", "patch"), default="patch")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        current = read_version()
        new = bump_version(current, args.part)
        if not args.dry_run:
            write_version(new)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(f"{current} -> {new}")
    else:
        print(new)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
