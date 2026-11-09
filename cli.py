"""Command-line triage, handy for demos and scripting.

    python cli.py triage "payments-api 5xx after deploy"          # memory ON
    python cli.py triage --no-memory "payments-api 5xx after deploy"
    python cli.py compare "payments-api 5xx after deploy"          # OFF vs ON
    python cli.py --offline compare "..."                         # no keys needed
"""
from __future__ import annotations

import argparse
import logging

from agent import triage
from config import load_settings
from incidents_data import seed_incidents
from llm import build_llm
from memory import InMemoryIncidentMemory, build_memory


def _print(title: str, res) -> None:
    print(f"\n===== {title} =====")
    for w in res.warnings:
        print(f"[warning] {w}")
    print(res.plan)
    if res.memories:
        print(f"\n-- {len(res.memories)} memories recalled --")
        for m in res.memories[:8]:
            print(f"* {m.text[:160]}")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING)
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--offline", action="store_true", help="use the in-memory demo store seeded with synthetic incidents")
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("triage")
    t.add_argument("alert")
    t.add_argument("--no-memory", action="store_true")
    c = sub.add_parser("compare")
    c.add_argument("alert")
    args = p.parse_args(argv)

    s = load_settings()
    if args.offline or not s.hindsight_ready:
        mem = InMemoryIncidentMemory()
        mem.retain_incidents(seed_incidents())
    else:
        mem = build_memory(s)
    llm = None if args.offline else build_llm(s)

    if args.cmd == "triage":
        _print("Memory OFF" if args.no_memory else f"Memory ON ({mem.name})", triage(args.alert, mem, llm, use_memory=not args.no_memory))
    else:
        _print("Memory OFF", triage(args.alert, mem, llm, use_memory=False))
        _print(f"Memory ON ({mem.name})", triage(args.alert, mem, llm, use_memory=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
