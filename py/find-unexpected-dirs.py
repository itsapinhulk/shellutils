#!/usr/bin/env python3
"""
Find non-empty directories matching given names anywhere under a root path.

By default looks for '.venv' and 'node_modules' directories. Once a
matching directory is found, it is not searched further (use --no-prune
to also find nested matches inside matched directories).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable, Iterator

DEFAULT_NAMES = [".venv", "node_modules"]


def find_unexpected_dirs(root: Path, names: Iterable[str], prune: bool = True) -> Iterator[Path]:
    """Yield non-empty directories under root whose name is in names."""
    names = set(names)
    for dirpath, dirnames, _filenames in os.walk(root):
        matched = [d for d in dirnames if d in names]
        for name in matched:
            full = Path(dirpath) / name
            try:
                if any(full.iterdir()):
                    yield full
            except OSError:
                continue
        if prune:
            dirnames[:] = [d for d in dirnames if d not in matched]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Root directory to search (default: current directory).",
    )
    parser.add_argument(
        "-n", "--name",
        action="append",
        dest="names",
        metavar="NAME",
        help=f"Directory name to match. May be repeated. "
             f"(default: {', '.join(DEFAULT_NAMES)})",
    )
    parser.add_argument(
        "--no-prune",
        action="store_true",
        help="Keep searching inside matched directories for nested matches.",
    )
    args = parser.parse_args()

    root = Path(args.path).expanduser()
    if not root.is_dir():
        sys.exit(f"Not a directory: {root}")

    names = args.names if args.names else DEFAULT_NAMES

    for d in sorted(find_unexpected_dirs(root, names, prune=not args.no_prune)):
        print(d)


if __name__ == "__main__":
    main()
