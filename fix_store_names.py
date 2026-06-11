"""One-off script to normalize team_head and rm_name in existing store_performance rows.

Reads rm_mapping.json and applies the same normalization the parser now does at ingest,
so historical rows match what future downloads will produce.

Usage:
  python3 fix_store_names.py [--dry-run]
"""
from __future__ import annotations

import sys

from src.db import init_db, get_conn
from src.name_mapping import normalize_team_head, normalize_rm_name


def main():
    dry_run = "--dry-run" in sys.argv
    init_db()

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT team_head, rm_name FROM store_performance"
        ).fetchall()

    updates_th: dict[str, str] = {}  # old -> canonical
    updates_rm: dict[str, str] = {}

    for team_head, rm_name in rows:
        canonical_th = normalize_team_head(team_head or "")
        if canonical_th != (team_head or "").strip():
            updates_th[team_head] = canonical_th

        canonical_rm = normalize_rm_name(rm_name or "")
        if canonical_rm != (rm_name or "").strip():
            updates_rm[rm_name] = canonical_rm

    print(f"team_head fixes: {len(updates_th)}")
    for old, new in sorted(updates_th.items()):
        print(f"  {old!r:30s} -> {new!r}")

    print(f"\nrm_name fixes: {len(updates_rm)}")
    for old, new in sorted(updates_rm.items()):
        print(f"  {old!r:30s} -> {new!r}")

    if not updates_th and not updates_rm:
        print("\nNothing to fix — all names already canonical.")
        return

    if dry_run:
        print("\n[dry-run] No changes written.")
        return

    with get_conn() as conn:
        for old, canonical in updates_th.items():
            conn.execute(
                "UPDATE store_performance SET team_head = ? WHERE team_head = ?",
                (canonical, old),
            )
        for old, canonical in updates_rm.items():
            conn.execute(
                "UPDATE store_performance SET rm_name = ? WHERE rm_name = ?",
                (canonical, old),
            )

    total = len(updates_th) + len(updates_rm)
    print(f"\nDone — applied {total} name fixes to store_performance.")


if __name__ == "__main__":
    main()
