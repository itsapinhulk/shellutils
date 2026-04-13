#!/usr/bin/env python3
"""
Back up specified files and directories into a target directory.

By default the script performs a dry run, printing what would be copied without
writing anything. Pass --save to actually write the files.

Paths under $HOME are placed under a '_tilde_' prefix, with leading dots in each
path component replaced by '_dot_'. Paths outside $HOME are stored relative to
the filesystem root with the same dot-encoding applied.

Example:
  ~/.config/nvim  ->  <target>/_tilde_/_dot_config/nvim
  ~/.bashrc       ->  <target>/_tilde_/_dot_bashrc
  /etc/hosts      ->  <target>/_root_/etc/hosts
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def encode_path(relative: Path) -> Path:
    """Replace leading dots in each component of a relative path with '_dot_'."""
    parts = []
    for part in relative.parts:
        if part.startswith("."):
            part = "_dot_" + part[1:]
        parts.append(part)
    return Path(*parts)


def backup(sources: list[str], target: str, save: bool = False) -> None:
    home = Path.home()
    target_dir = Path(target).expanduser().resolve()

    print(f"Target: {target_dir}\n")

    for src_str in sources:
        src = Path(src_str).expanduser().resolve()

        try:
            rel = src.relative_to(home)
            dest = target_dir / "_tilde_" / encode_path(rel)
        except ValueError:
            # Not under home; store relative to filesystem root.
            dest = target_dir / "_root_" / encode_path(src.relative_to("/"))

        dest_display = "<target>" / dest.relative_to(target_dir)

        if src.is_file():
            print(f"  {src} -> {dest_display}")
            if save:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
        elif src.is_dir():
            print(f"  {src}/ -> {dest_display}/")
            if save:
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(src, dest)
        else:
            print(f"Warning: {src} does not exist or is not a file/directory, skipping.",
                  file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Back up files and directories into a target directory.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s -s ~/.bashrc ~/.config/nvim -t /mnt/backup             # dry run
  %(prog)s -s ~/.bashrc ~/.config/nvim -t /mnt/backup --save      # write files
  %(prog)s --sources ~/.ssh ~/.zshrc /etc/hosts --target /tmp/backup-dotfiles --save

Output layout:
  ~/.bashrc        ->  <target>/_tilde_/_dot_bashrc
  ~/.config/nvim   ->  <target>/_tilde_/_dot_config/nvim
  /etc/hosts       ->  <target>/_root_/etc/hosts
        """,
    )
    parser.add_argument(
        "-s", "--sources",
        nargs="+",
        required=True,
        metavar="PATH",
        help="Files or directories to back up (any absolute or home-relative path).",
    )
    parser.add_argument(
        "-t", "--target",
        required=True,
        metavar="DIR",
        help="Destination directory (created if it does not exist).",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Actually write files. Without this flag the script only prints what would be backed up.",
    )

    args = parser.parse_args()
    if not args.save:
        print("Dry run — pass --save to write files.\n")

    backup(args.sources, args.target, save=args.save)


if __name__ == "__main__":
    main()
