"""Update checker tests: version comparison, offline path and update guards."""

import json
import subprocess
from pathlib import Path

import pytest

from klinescan import updates
from klinescan.__version__ import __version__


class _Response:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(mapping, calls=None):
    def opener(request, timeout):
        url = getattr(request, "full_url", request)
        if calls is not None:
            calls.append(url)
        if isinstance(mapping, Exception):
            raise mapping
        return _Response(mapping[url])
    return opener


def test_version_file_matches_package():
    root = Path(__file__).resolve().parents[1]
    assert (root / "version.txt").read_text(encoding="utf-8").strip() == __version__


def test_version_comparison():
    assert updates.is_newer("0.2.0", "0.1.0")
    assert updates.is_newer("1.0", "0.9.9")
    assert updates.is_newer("v0.1.1", "0.1.0")
    assert not updates.is_newer("0.1.0", "0.1.0")
    assert not updates.is_newer("0.0.9", "0.1.0")
    assert not updates.is_newer("not-a-version", "0.1.0")
    assert updates.version_tuple("v1.2.3-beta") == (1, 2, 3)


def test_check_offline_is_graceful():
    info = updates.check_for_update(opener=_opener(OSError("no network")))
    assert not info.available
    assert info.error
    assert info.local == __version__


def test_check_new_version_lists_commits():
    mapping = {
        updates.VERSION_URL: b"9.9.9\n",
        updates.COMMITS_URL: json.dumps([
            {"commit": {"message": "Add a feature\n\nlong body"}},
            {"commit": {"message": "Fix a bug"}},
        ]).encode(),
    }
    info = updates.check_for_update(opener=_opener(mapping))
    assert info.available and info.remote == "9.9.9"
    assert info.commits == ["Add a feature", "Fix a bug"]
    assert info.ok


def test_check_same_version_does_not_offer_update():
    info = updates.check_for_update(opener=_opener({updates.VERSION_URL: __version__.encode()}))
    assert not info.available and not info.error


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_git_update_refuses_dirty_tree(tmp_path):
    calls = []

    def runner(cmd, cwd):
        calls.append(cmd)
        return _completed(stdout=" M README.md\n")

    with pytest.raises(updates.UpdateError, match="local changes"):
        updates.apply_git_update(tmp_path, runner)
    assert calls == [["git", "status", "--porcelain"]]


def test_git_update_pulls_and_installs(tmp_path):
    calls = []

    def runner(cmd, cwd):
        calls.append(cmd)
        return _completed()

    updates.apply_git_update(tmp_path, runner)
    assert calls[0] == ["git", "status", "--porcelain"]
    assert calls[1] == ["git", "pull", "--ff-only", "origin", "main"]
    assert calls[2][1:] == ["-m", "pip", "install", "-r", "requirements.txt"]


def test_git_update_reports_pull_failure(tmp_path):
    def runner(cmd, cwd):
        if cmd[:2] == ["git", "status"]:
            return _completed()
        if cmd[:2] == ["git", "pull"]:
            return _completed(returncode=1, stderr="not possible")
        return _completed()

    with pytest.raises(updates.UpdateError, match="git pull failed"):
        updates.apply_git_update(tmp_path, runner)


def test_archive_update_rejects_invalid_payload(tmp_path):
    with pytest.raises(updates.UpdateError):
        updates.apply_archive_update(tmp_path, opener=_opener({updates.ARCHIVE_URL: b"not a tarball"}))
