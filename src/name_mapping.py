"""Name alias and normalization helpers for HKTVmall sales team.

Loads data/rm_mapping.json which is the single source of truth.
"""
from __future__ import annotations

import json
import os

_MAPPING_PATH = os.path.join(os.path.dirname(__file__), "rm_mapping.json")

_data: dict | None = None


def _load() -> dict:
    global _data
    if _data is None:
        with open(_MAPPING_PATH, encoding="utf-8") as f:
            _data = json.load(f)
    return _data


def _th_variant_map() -> dict[str, str]:
    """db_variant → canonical for team heads."""
    m = {}
    for th in _load()["team_heads"]:
        for v in th["db_variants"]:
            m[v.strip().lower()] = th["canonical"]
    return m


def _rm_variant_map() -> dict[str, str]:
    """db_variant → canonical for RM names."""
    m = {}
    for rm in _load()["rms"]:
        for v in rm["db_variants"]:
            m[v.strip().lower()] = rm["canonical"]
    return m


_TH_VARIANTS: dict[str, str] | None = None
_RM_VARIANTS: dict[str, str] | None = None


def normalize_team_head(name: str) -> str:
    """Map any known team head variant/alias to its canonical name."""
    global _TH_VARIANTS
    if _TH_VARIANTS is None:
        _TH_VARIANTS = _th_variant_map()
    return _TH_VARIANTS.get((name or "").strip().lower(), (name or "").strip())


def normalize_rm_name(name: str) -> str:
    """Map any known RM name variant to its canonical name."""
    global _RM_VARIANTS
    if _RM_VARIANTS is None:
        _RM_VARIANTS = _rm_variant_map()
    return _RM_VARIANTS.get((name or "").strip().lower(), (name or "").strip())


def get_bot_alias_block() -> str:
    """Return a plain-text block describing all aliases for the bot system prompt."""
    data = _load()

    th_lines = []
    for th in data["team_heads"]:
        if th["aliases"] and th["aliases"] != [th["canonical"]]:
            for a in th["aliases"]:
                if a != th["canonical"]:
                    th_lines.append(f"{a} = {th['canonical']}")

    rm_lines = []
    for rm in data["rms"]:
        for a in rm["aliases"]:
            if a and a != rm["canonical"]:
                rm_lines.append(f"{a} = {rm['canonical']}")

    parts = []
    if th_lines:
        parts.append("Team Head aliases: " + ", ".join(sorted(th_lines)))
    if rm_lines:
        parts.append("RM aliases: " + ", ".join(sorted(rm_lines)))

    return "\n".join(parts)
