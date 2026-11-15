"""Stable identifiers used by diagnostic packs and catalog imports."""

import re


def stable_id(*parts: str) -> str:
    """Return a readable, deterministic, namespaced identifier."""
    value = ".".join(str(part or "unknown").strip().lower() for part in parts)
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "unknown"
