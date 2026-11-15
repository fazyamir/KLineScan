"""Self-update support for KLineScan.

The check is deliberately small and fail-safe: it fetches the version published
on the public ``main`` branch, compares it with the running version, and (when
the user agrees) updates either through ``git pull --ff-only`` or by unpacking
the ``main`` archive.  Network and permission errors never propagate into the
UI; they are reported as an ``UpdateInfo`` error and logged.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from klinescan.__version__ import __version__

log = logging.getLogger(__name__)

REPO = "fazyamir/KLineScan"
VERSION_URL = f"https://raw.githubusercontent.com/{REPO}/main/version.txt"
COMMITS_URL = f"https://api.github.com/repos/{REPO}/commits?sha=main&per_page=20"
ARCHIVE_URL = f"https://github.com/{REPO}/archive/refs/heads/main.tar.gz"

_VERSION_RE = re.compile(r"^\s*v?(\d+(?:\.\d+)*)(?:[-+].*)?\s*$")


class UpdateError(RuntimeError):
    """An update could not be applied; the message is safe to show to the user."""


@dataclass
class UpdateInfo:
    local: str = __version__
    remote: str = ""
    available: bool = False
    commits: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def version_tuple(value: str) -> tuple[int, ...]:
    """Parse ``1.2.3`` (optionally prefixed with ``v``) into a comparable tuple."""
    match = _VERSION_RE.match(str(value or ""))
    if not match:
        raise ValueError(f"invalid version {value!r}")
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(remote: str, local: str) -> bool:
    """True when ``remote`` is a strictly newer version than ``local``."""
    try:
        return version_tuple(remote) > version_tuple(local)
    except ValueError:
        return False


def _fetch(url: str, timeout: float, opener: Callable) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": f"KLineScan/{__version__}"})
    with opener(request, timeout=timeout) as response:
        return response.read()


def check_for_update(
    *,
    timeout: float = 6.0,
    opener: Callable = urllib.request.urlopen,
) -> UpdateInfo:
    """Fetch the published version and commit list.

    Never raises: network failures, rate limits and malformed responses are
    returned as ``UpdateInfo.error``.  Tests inject ``opener`` to stay offline.
    """
    info = UpdateInfo(local=__version__)
    try:
        text = _fetch(VERSION_URL, timeout, opener).decode("utf-8", "replace").strip()
    except Exception as exc:  # noqa: BLE001 - the UI must keep working offline
        info.error = f"update check failed: {exc}"
        log.info("update check failed: %s", exc)
        return info
    info.remote = text
    if not is_newer(text, __version__):
        return info
    info.available = True
    try:
        payload = json.loads(_fetch(COMMITS_URL, timeout, opener).decode("utf-8", "replace"))
        for item in payload if isinstance(payload, list) else []:
            message = ((item.get("commit") or {}).get("message") or "").strip()
            if message:
                info.commits.append(message.splitlines()[0])
    except Exception as exc:  # noqa: BLE001 - commit list is optional
        log.info("could not read commit list: %s", exc)
    return info


def _default_run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, check=False)


def is_git_checkout(root: Path) -> bool:
    return (Path(root) / ".git").exists() and shutil.which("git") is not None


def apply_git_update(root: str | Path, runner: Callable = _default_run) -> None:
    """Update a git clone with ``git pull --ff-only origin main``.

    Refuses to touch a dirty working tree.  ``runner`` is injectable for tests.
    """
    root = Path(root)
    status = runner(["git", "status", "--porcelain"], root)
    if status.returncode != 0:
        raise UpdateError("git status failed; update not applied")
    if (status.stdout or "").strip():
        raise UpdateError("local changes present; commit or stash them before updating")
    pull = runner(["git", "pull", "--ff-only", "origin", "main"], root)
    if pull.returncode != 0:
        raise UpdateError(f"git pull failed: {(pull.stderr or pull.stdout or '').strip()}")
    install = runner([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], root)
    if install.returncode != 0:
        raise UpdateError(f"dependency install failed: {(install.stderr or install.stdout or '').strip()}")


def _extract_verified(data: bytes, destination: Path) -> Path:
    """Extract a verified main archive and return its single top-level directory."""
    try:
        archive = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
    except (tarfile.TarError, OSError) as exc:
        raise UpdateError(f"downloaded archive is not a valid tar.gz: {exc}") from exc
    with archive:
        members = archive.getmembers()
        names = {member.name.lstrip("./") for member in members}
        required = ("main.py", "requirements.txt", "klinescan/__init__.py", "version.txt")
        tops = {name.split("/", 1)[0] for name in names if "/" in name}
        if len(tops) != 1:
            raise UpdateError("archive does not contain a single project directory")
        top = tops.pop()
        missing = [rel for rel in required if f"{top}/{rel}" not in names]
        if missing:
            raise UpdateError(f"archive is missing expected files: {', '.join(missing)}")
        _safe_extract(archive, members, destination)
    return destination / top


def _safe_extract(archive: tarfile.TarFile, members: Iterable[tarfile.TarInfo], destination: Path) -> None:
    destination = destination.resolve()
    for member in members:
        target = (destination / member.name).resolve()
        if destination not in target.parents and target != destination:
            raise UpdateError(f"unsafe path in archive: {member.name}")
        if member.issym() or member.islnk():
            raise UpdateError(f"links are not allowed in the update archive: {member.name}")
    archive.extractall(destination, members=members)


def _copy_tree(source: Path, destination: Path) -> None:
    for item in source.iterdir():
        target = destination / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)


def apply_archive_update(
    root: str | Path,
    *,
    timeout: float = 60.0,
    opener: Callable = urllib.request.urlopen,
) -> None:
    """Download, verify and unpack the ``main`` archive over an installed copy."""
    root = Path(root)
    data = _fetch(ARCHIVE_URL, timeout, opener)
    if not data:
        raise UpdateError("downloaded archive is empty")
    with tempfile.TemporaryDirectory(prefix="klinescan-update-") as tmp:
        tmp_path = Path(tmp)
        extracted = _extract_verified(data, tmp_path / "new")
        backup = tmp_path / "backup"
        backup.mkdir()
        ignore = shutil.ignore_patterns(".git", "venv", ".venv", "__pycache__", ".pytest_cache",
                                        "logs", "*.pyc")
        shutil.copytree(root, backup / "tree", ignore=ignore)
        try:
            _copy_tree(extracted, root)
        except Exception as exc:  # noqa: BLE001 - roll back and re-raise
            try:
                _copy_tree(backup / "tree", root)
            except Exception as rollback_exc:  # pragma: no cover - best effort
                log.error("rollback failed: %s", rollback_exc)
            raise UpdateError(f"update failed and was rolled back: {exc}") from exc


def restart_app() -> None:
    """Replace the current process with a fresh KLineScan instance."""
    python = sys.executable
    os.execv(python, [python, *sys.argv])


def apply_update(root: str | Path, *, runner: Callable = _default_run, opener: Callable = urllib.request.urlopen) -> None:
    """Apply the best available update method for ``root``."""
    if is_git_checkout(Path(root)):
        apply_git_update(root, runner)
    else:
        apply_archive_update(root, opener=opener)
