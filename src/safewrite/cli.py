"""CLI: write stdin to a file atomically (a `sponge` workalike from moreutils).

    grep -v DEBUG app.log | safewrite app.log
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import BinaryIO, Optional, Sequence

from . import __version__
from .core import write_bytes


def _permissions(value: str) -> int:
    """Parse --perms: an octal mask such as 600, 0600 or 0o600."""
    try:
        parsed = int(value, 8)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected an octal mask like 600, got {value!r}")
    if not 0 <= parsed <= 0o7777:
        raise argparse.ArgumentTypeError(f"permission mask out of range: {value!r}")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="safewrite",
        description="Read stdin to the end and write it to FILE atomically.",
    )
    parser.add_argument("file", metavar="FILE", help="destination file")
    parser.add_argument(
        "-a",
        "--append",
        action="store_true",
        help="append to the file, keeping its previous content",
    )
    parser.add_argument(
        "-n",
        "--no-clobber",
        action="store_true",
        help="do not overwrite an existing file (exits with code 2)",
    )
    parser.add_argument(
        "-p",
        "--perms",
        type=_permissions,
        metavar="MODE",
        help="permissions for the result, e.g. 600 (default: keep the existing file's mode)",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="allow empty input to truncate a non-empty file (refused by default)",
    )
    parser.add_argument(
        "--no-fsync",
        action="store_true",
        help="skip fsync (durable=False in the API): faster, but a power loss may lose the data",
    )
    parser.add_argument("-V", "--version", action="version", version=f"safewrite {__version__}")
    return parser


def _read_existing(path: str) -> bytes:
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except FileNotFoundError:
        return b""


def _target_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def main(argv: Optional[Sequence[str]] = None, stdin: Optional[BinaryIO] = None) -> int:
    args = build_parser().parse_args(argv)
    source = stdin if stdin is not None else sys.stdin.buffer

    try:
        payload = source.read()

        if not payload and not args.append and not args.allow_empty and _target_size(args.file):
            print(
                f"safewrite: refusing to truncate '{args.file}' with empty input"
                " (the command upstream may have failed; pass --allow-empty to force)",
                file=sys.stderr,
            )
            return 3

        if args.append:
            payload = _read_existing(args.file) + payload
        write_bytes(
            args.file,
            payload,
            perms=args.perms,
            overwrite=not args.no_clobber,
            durable=not args.no_fsync,
        )
    except OSError as exc:
        reason = exc.strerror or str(exc)
        print(f"safewrite: cannot write '{args.file}': {reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
