from pathlib import Path

import pytest

from tools.bump_version import bump_version, read_version, update_version


def make_tree(root: Path, version: str = "1.2.3") -> None:
    (root / "klinescan").mkdir()
    (root / "klinescan" / "__version__.py").write_text(
        f'"""Version."""\n\n__version__ = "{version}"\n', encoding="utf-8"
    )
    (root / "version.txt").write_text(f"{version}\n", encoding="utf-8")


@pytest.mark.parametrize(
    ("current", "part", "expected"),
    [
        ("1.2.3", "patch", "1.2.4"),
        ("1.2.3", "minor", "1.3.0"),
        ("1.2.3", "major", "2.0.0"),
        ("0.1.9", "patch", "0.1.10"),
    ],
)
def test_bump_version(current: str, part: str, expected: str) -> None:
    assert bump_version(current, part) == expected


def test_update_version_writes_both_files(tmp_path: Path) -> None:
    make_tree(tmp_path)
    assert update_version(tmp_path, "patch") == "1.2.4"
    assert read_version(tmp_path) == "1.2.4"
    assert (tmp_path / "version.txt").read_text(encoding="utf-8") == "1.2.4\n"


def test_bump_rejects_bad_version() -> None:
    with pytest.raises(ValueError):
        bump_version("1.2", "patch")
    with pytest.raises(ValueError):
        bump_version("1.2.3", "build")
