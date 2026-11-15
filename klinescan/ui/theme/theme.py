"""Token-driven theme loading and QSS rendering for KLineScan."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication


class ThemeError(RuntimeError):
    """Raised when a theme cannot be loaded or rendered."""


THEME_ALIASES = {
    "dark_workshop": "graphite",
    "dark": "graphite",
    "light_clean": "daylight",
    "light": "daylight",
}

_PLACEHOLDER = re.compile(r"@([A-Za-z0-9_.-]+)@")


def _theme_root() -> Path:
    return Path(__file__).resolve().parent


def _tokens_dir() -> Path:
    return _theme_root() / "tokens"


def _qss_path() -> Path:
    return _theme_root() / "qss" / "app.qss"


def normalize_theme_name(name: str) -> str:
    normalized = (name or "graphite").strip().lower().replace(" ", "_")
    return THEME_ALIASES.get(normalized, normalized)


def available_themes() -> Tuple[str, ...]:
    return tuple(sorted(path.stem for path in _tokens_dir().glob("*.json")))


def load_tokens(name: str) -> Dict[str, str]:
    theme_name = normalize_theme_name(name)
    path = _tokens_dir() / f"{theme_name}.json"
    if not path.exists():
        known = ", ".join(available_themes()) or "none"
        raise ThemeError(f"Unknown theme '{name}'. Available themes: {known}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ThemeError(f"Could not load theme '{theme_name}': {exc}") from exc

    tokens: Dict[str, str] = {}

    def flatten(value: Any, prefix: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                flatten(child, f"{prefix}.{key}" if prefix else str(key))
            return
        tokens[prefix] = str(value)

    flatten(raw)
    tokens.pop("name", None)
    return tokens


def render_theme(name: str) -> Tuple[Dict[str, str], str]:
    tokens = load_tokens(name)
    template = _qss_path().read_text(encoding="utf-8")
    missing: list[str] = []

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in tokens:
            missing.append(key)
            return match.group(0)
        return tokens[key]

    rendered = _PLACEHOLDER.sub(replace, template)
    if missing:
        unique = ", ".join(sorted(set(missing)))
        raise ThemeError(f"Theme '{normalize_theme_name(name)}' is missing tokens: {unique}")
    return tokens, rendered


class ThemeManager(QObject):
    """Applies a token theme to the active QApplication and exposes colors."""

    themeChanged = pyqtSignal(str)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._current = ""
        self._tokens: Dict[str, str] = {}
        self._qss = ""

    @property
    def current(self) -> str:
        return self._current

    @property
    def tokens(self) -> Dict[str, str]:
        return dict(self._tokens)

    @property
    def stylesheet(self) -> str:
        return self._qss

    def color(self, key: str, fallback: str = "#FF00FF") -> str:
        return self._tokens.get(key, fallback)

    def apply(self, name: str) -> str:
        tokens, rendered = render_theme(name)
        self._current = normalize_theme_name(name)
        self._tokens = tokens
        self._qss = rendered
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(rendered)
        self.themeChanged.emit(self._current)
        return rendered


_default_manager: Optional[ThemeManager] = None


def get_theme_manager() -> ThemeManager:
    global _default_manager
    if _default_manager is None:
        _default_manager = ThemeManager()
    return _default_manager
