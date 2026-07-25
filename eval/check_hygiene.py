#!/usr/bin/env python3
"""Hygiene check: only cli.py may use sys.path.insert."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "scripts"
ALLOWED = {"cli.py"}


def main():
    offenders = []
    for p in ROOT.rglob("*.py"):
        if p.name in ALLOWED:
            continue
        text = p.read_text(encoding="utf-8")
        if "sys.path.insert" in text:
            offenders.append(str(p.relative_to(ROOT.parent)))

    if offenders:
        print("stray sys.path.insert in:", offenders)
        sys.exit(1)
    print("clean")


if __name__ == "__main__":
    main()
