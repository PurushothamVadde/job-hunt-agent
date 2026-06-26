"""ATS field-map registry.

Maps a detected ATS platform name to its CSS-selector field map. The
``generic`` fallback uses label-keyword heuristics instead of fixed selectors.
"""

from __future__ import annotations

from typing import Any

from . import generic, greenhouse, lever, workday

REGISTRY: dict[str, dict[str, Any]] = {
    "greenhouse": greenhouse.FIELD_MAP,
    "lever": lever.FIELD_MAP,
    "workday": workday.FIELD_MAP,
}


def get_field_map(platform: str) -> dict[str, Any]:
    """Return the field map for ``platform`` (falls back to generic keywords)."""
    return REGISTRY.get(platform, {})


__all__ = ["REGISTRY", "get_field_map", "generic", "greenhouse", "lever", "workday"]
