"""Seed the Hindsight bank with SYNTHETIC past incidents.

Usage:  python seed.py            # retain all demo incidents
        python seed.py --dry-run  # print what would be stored
"""
from __future__ import annotations

import argparse
import sys

from config import load_settings
from incidents_data import seed_incidents
from memory import HindsightIncidentMemory, MemoryError_


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--batch-size", type=int, default=6)
    args = p.parse_args(argv)

    incidents = seed_incidents()
    if args.dry_run:
        for inc in incidents:
            print(f"--- {inc.incident_id} @ {inc.occurred_at.isoformat()} tags={inc.tags()}\n{inc.to_memory_text()}\n")
        return 0

    s = load_settings()
    if not s.hindsight_ready:
        print("HINDSIGHT_API_KEY is not set (see .env.example).", file=sys.stderr)
        return 2
    mem = HindsightIncidentMemory(s.hindsight_base_url, s.hindsight_api_key, s.bank_id)
    mem.ensure_bank()
    total = 0
    for i in range(0, len(incidents), args.batch_size):
        chunk = incidents[i : i + args.batch_size]
        try:
            total += mem.retain_incidents(chunk)
        except MemoryError_ as exc:
            print(f"Failed on batch starting {chunk[0].incident_id}: {exc}", file=sys.stderr)
            return 1
        print(f"Retained {chunk[0].incident_id}..{chunk[-1].incident_id}")
    print(f"Done: {total} incidents retained into bank '{s.bank_id}' at {s.hindsight_base_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
