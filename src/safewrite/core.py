"""Atomic file writes: the content is replaced in full, or not replaced at all."""

from __future__ import annotations

import os
import stat
import tempfile
from contextlib import contextmanager
from typing import IO, Any, Iterator, Optional, Union

__all__ = ["atomic_write", "write_bytes", "write_text"]

StrPath = Union[str, "os.PathLike[str]"]

_TEXT_MODES = frozenset({"w", "wt", "tw"})
_BINARY_MODES = frozenset({"wb", "bw"})


def _read_umask() -> int:
    """The current umask, re-read on every write so a changed umask is respected."""
    try:
        with open("/proc/self/status", encoding="ascii") as status:
            for line in status:
                if line.startswith("Umask:"):
                    return int(line.split()[1], 8)
    except (OSError, ValueError, IndexError):  # pragma: no cover - non-Linux platforms
        pass
    current = os.umask(0o022)
    os.umask(current)
    return current


def _file_mode_from_umask() -> int:
    return 0o666 & ~_read_umask()


def _resolve_target(path: StrPath, follow_symlinks: bool) -> str:
    target = os.fspath(path)
    if follow_symlinks and os.path.islink(target):
        target = os.path.realpath(target)
    return target


def _prepare_permissions(tmp_path: str, target: str, perms: Optional[int]) -> int:
    """Set the final permissions before any data is written and return the mode."""
    if perms is not None:
        mode = perms
    else:
        try:
            target_stat = os.stat(target)
        except FileNotFoundError:
            mode = _file_mode_from_umask()
        else:
            mode = stat.S_IMODE(target_stat.st_mode)
    try:
        os.chmod(tmp_path, mode)
    except OSError:  # pragma: no cover - filesystems without chmod support
        pass
    return mode


def _restore_special_bits(target: str, mode: int) -> None:
    """Re-apply setuid/setgid, which the kernel clears whenever a file is written."""
    try:
        os.chmod(target, mode)
    except OSError:  # pragma: no cover - platforms without setuid semantics
        pass


def _fsync_directory(directory: str) -> None:
    if os.name != "posix":  # pragma: no cover - platform dependent
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _commit(tmp_path: str, target: str, overwrite: bool) -> None:
    if overwrite:
        os.replace(tmp_path, target)
        return
    if os.name == "posix":
        try:
            # link() fails with FileExistsError if the target exists — atomically.
            os.link(tmp_path, target)
        except OSError as exc:
            if isinstance(exc, FileExistsError):
                raise
            if os.path.exists(target):
                raise FileExistsError(target) from exc
            os.replace(tmp_path, target)
        else:
            os.unlink(tmp_path)
    else:  # pragma: no cover - on Windows rename itself fails on an existing target
        os.rename(tmp_path, target)


@contextmanager
def atomic_write(
    path: StrPath,
    mode: str = "w",
    *,
    encoding: Optional[str] = None,
    errors: Optional[str] = None,
    newline: Optional[str] = None,
    perms: Optional[int] = None,
    overwrite: bool = True,
    durable: bool = True,
    follow_symlinks: bool = True,
) -> Iterator[IO[Any]]:
    """Write a file in full, or leave it untouched.

    Until the block completes successfully, ``path`` still holds the previous content.
    Any exception inside the block cancels the write and removes the temporary file.

    :param mode: ``"w"``/``"wt"`` for text, ``"wb"`` for bytes.
    :param perms: explicit permissions for the result, e.g. ``0o600``. By default an
        existing file keeps its own mode and a new one gets ``0o666 & ~umask``. The
        value is applied before the first write, so the file is never briefly readable
        by others.
    :param overwrite: when ``False``, an existing target raises ``FileExistsError``.
    :param durable: ``fsync`` the file and the directory — the write survives a power
        loss, at the cost of a much slower write.
    :param follow_symlinks: write through to the link target instead of replacing the
        symlink itself.

    >>> import json, tempfile
    >>> from pathlib import Path
    >>> path = Path(tempfile.mkdtemp()) / "config.json"
    >>> with atomic_write(path, encoding="utf-8") as f:
    ...     json.dump({"ok": True}, f)
    >>> path.read_text(encoding="utf-8")
    '{"ok": true}'
    """
    if mode in _TEXT_MODES:
        binary = False
    elif mode in _BINARY_MODES:
        binary = True
    else:
        raise ValueError(f"only write modes 'w', 'wt' and 'wb' are supported, got {mode!r}")
    if binary and (encoding is not None or errors is not None or newline is not None):
        raise ValueError("encoding/errors/newline do not apply to binary mode")
    if perms is not None and not 0 <= perms <= 0o7777:
        raise ValueError(f"perms must be a permission bitmask like 0o600, got {perms!r}")

    target = _resolve_target(path, follow_symlinks)
    directory = os.path.dirname(target) or os.curdir

    fd, tmp_path = tempfile.mkstemp(
        dir=directory, prefix=f".{os.path.basename(target)}.", suffix=".tmp"
    )
    try:
        file_mode = _prepare_permissions(tmp_path, target, perms)
        if binary:
            handle = os.fdopen(fd, mode)
        else:
            handle = os.fdopen(fd, mode, encoding=encoding, errors=errors, newline=newline)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        _silent_unlink(tmp_path)
        raise

    try:
        with handle:
            yield handle
            handle.flush()
            if durable:
                os.fsync(handle.fileno())
        _commit(tmp_path, target, overwrite)
        if file_mode & 0o7000:
            _restore_special_bits(target, file_mode)
    except BaseException:
        _silent_unlink(tmp_path)
        raise

    if durable:
        _fsync_directory(directory)


def _silent_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def write_text(
    path: StrPath,
    data: str,
    *,
    encoding: str = "utf-8",
    errors: Optional[str] = None,
    newline: Optional[str] = None,
    perms: Optional[int] = None,
    overwrite: bool = True,
    durable: bool = True,
    follow_symlinks: bool = True,
) -> None:
    """Atomic counterpart of ``Path.write_text()``."""
    with atomic_write(
        path,
        "w",
        encoding=encoding,
        errors=errors,
        newline=newline,
        perms=perms,
        overwrite=overwrite,
        durable=durable,
        follow_symlinks=follow_symlinks,
    ) as handle:
        handle.write(data)


def write_bytes(
    path: StrPath,
    data: bytes,
    *,
    perms: Optional[int] = None,
    overwrite: bool = True,
    durable: bool = True,
    follow_symlinks: bool = True,
) -> None:
    """Atomic counterpart of ``Path.write_bytes()``."""
    with atomic_write(
        path,
        "wb",
        perms=perms,
        overwrite=overwrite,
        durable=durable,
        follow_symlinks=follow_symlinks,
    ) as handle:
        handle.write(data)
