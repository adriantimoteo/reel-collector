#!/usr/bin/env python3
"""Fail if any blocklisted (telemetry/analytics) package is in the lockfile.

GC Reel Map's privacy promise (D11) forbids analytics/telemetry in any form,
including inside tooling. This is run in CI against uv.lock.
"""

import sys
import tomllib
from pathlib import Path

DEFAULT_LOCKFILE = Path("uv.lock")
DEFAULT_BLOCKLIST = Path("scripts/telemetry-blocklist.txt")


def normalize(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


def load_blocklist(path: Path) -> set[str]:
    names: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        names.add(normalize(line))
    return names


def load_lockfile_packages(path: Path) -> set[str]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return {normalize(pkg["name"]) for pkg in data.get("package", []) if "name" in pkg}


def find_blocked(lockfile: Path, blocklist: Path) -> list[str]:
    blocked_names = load_blocklist(blocklist)
    present = load_lockfile_packages(lockfile)
    return sorted(present & blocked_names)


def main(argv: list[str] | None = None) -> int:
    argv = list(argv) if argv is not None else sys.argv[1:]
    lockfile = Path(argv[0]) if len(argv) > 0 else DEFAULT_LOCKFILE
    blocklist = Path(argv[1]) if len(argv) > 1 else DEFAULT_BLOCKLIST

    blocked = find_blocked(lockfile, blocklist)
    if blocked:
        print("Blocklisted telemetry/analytics packages found in the lockfile:")
        for name in blocked:
            print(f"  - {name}")
        print("GC Reel Map's privacy promise forbids these. Remove the dependency.")
        return 1
    print("No blocklisted telemetry/analytics packages found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
