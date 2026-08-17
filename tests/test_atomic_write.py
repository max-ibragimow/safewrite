from __future__ import annotations

import doctest
import os
import stat

import pytest

from safewrite import atomic_write, core, write_bytes, write_text


def _leftovers(directory) -> list[str]:
    return [p.name for p in directory.iterdir() if p.name.endswith(".tmp")]


def test_doctests_pass():
    assert doctest.testmod(core, verbose=False).failed == 0


def test_creates_new_file(tmp_path):
    target = tmp_path / "new.txt"
    with atomic_write(target, encoding="utf-8") as handle:
        handle.write("hello")
    assert target.read_text(encoding="utf-8") == "hello"
    assert _leftovers(tmp_path) == []


def test_replaces_existing_content_entirely(tmp_path):
    target = tmp_path / "config.json"
    target.write_text("a much longer previous content", encoding="utf-8")
    with atomic_write(target, encoding="utf-8") as handle:
        handle.write("new")
    assert target.read_text(encoding="utf-8") == "new"


def test_writes_non_ascii_text(tmp_path):
    target = tmp_path / "utf8.txt"
    payload = "héllo wörld — ok"
    write_text(target, payload)
    assert target.read_text(encoding="utf-8") == payload


def test_old_content_visible_until_block_finishes(tmp_path):
    target = tmp_path / "state.txt"
    target.write_text("old", encoding="utf-8")
    with atomic_write(target, encoding="utf-8") as handle:
        handle.write("new")
        handle.flush()
        assert target.read_text(encoding="utf-8") == "old"
    assert target.read_text(encoding="utf-8") == "new"


def test_exception_leaves_original_untouched(tmp_path):
    target = tmp_path / "config.json"
    target.write_text("old", encoding="utf-8")
    with pytest.raises(RuntimeError):
        with atomic_write(target, encoding="utf-8") as handle:
            handle.write("partially written")
            raise RuntimeError("failure in the middle of a write")
    assert target.read_text(encoding="utf-8") == "old"
    assert _leftovers(tmp_path) == []


def test_keyboard_interrupt_also_cleans_up(tmp_path):
    target = tmp_path / "config.json"
    target.write_text("old", encoding="utf-8")
    with pytest.raises(KeyboardInterrupt):
        with atomic_write(target, encoding="utf-8") as handle:
            handle.write("partial")
            raise KeyboardInterrupt
    assert target.read_text(encoding="utf-8") == "old"
    assert _leftovers(tmp_path) == []


def test_failure_on_new_file_leaves_no_file(tmp_path):
    target = tmp_path / "new.txt"
    with pytest.raises(RuntimeError):
        with atomic_write(target, encoding="utf-8") as handle:
            handle.write("garbage")
            raise RuntimeError
    assert not target.exists()
    assert _leftovers(tmp_path) == []


def test_binary_mode(tmp_path):
    target = tmp_path / "blob.bin"
    with atomic_write(target, "wb") as handle:
        handle.write(b"\x00\xff\x10")
    assert target.read_bytes() == b"\x00\xff\x10"


@pytest.mark.parametrize("mode", ["r", "a", "w+", "rb", "x"])
def test_rejects_non_write_modes(tmp_path, mode):
    with pytest.raises(ValueError, match="write modes"):
        with atomic_write(tmp_path / "f", mode):
            pass


def test_rejects_encoding_in_binary_mode(tmp_path):
    with pytest.raises(ValueError, match="binary mode"):
        with atomic_write(tmp_path / "f", "wb", encoding="utf-8"):
            pass


def test_no_clobber_raises_and_keeps_original(tmp_path):
    target = tmp_path / "once.txt"
    target.write_text("original", encoding="utf-8")
    with pytest.raises(FileExistsError):
        with atomic_write(target, encoding="utf-8", overwrite=False) as handle:
            handle.write("overwrite")
    assert target.read_text(encoding="utf-8") == "original"
    assert _leftovers(tmp_path) == []


def test_no_clobber_creates_missing_file(tmp_path):
    target = tmp_path / "once.txt"
    with atomic_write(target, encoding="utf-8", overwrite=False) as handle:
        handle.write("first")
    assert target.read_text(encoding="utf-8") == "first"
    assert _leftovers(tmp_path) == []


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_preserves_permissions_of_existing_file(tmp_path):
    target = tmp_path / "shared.conf"
    target.write_text("data", encoding="utf-8")
    target.chmod(0o640)
    write_text(target, "new data")
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_new_file_gets_umask_permissions_not_0600(tmp_path):
    target = tmp_path / "fresh.conf"
    write_text(target, "data")
    reference = tmp_path / "reference.conf"
    reference.write_text("data", encoding="utf-8")
    assert stat.S_IMODE(target.stat().st_mode) == stat.S_IMODE(reference.stat().st_mode)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_umask_is_re_read_on_every_write(tmp_path):
    previous = os.umask(0o077)
    try:
        write_text(tmp_path / "secret.txt", "data")
        assert stat.S_IMODE((tmp_path / "secret.txt").stat().st_mode) == 0o600
        os.umask(0o022)
        write_text(tmp_path / "public.conf", "data")
        assert stat.S_IMODE((tmp_path / "public.conf").stat().st_mode) == 0o644
    finally:
        os.umask(previous)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_explicit_perms_win_over_umask_and_existing_mode(tmp_path):
    new_file = tmp_path / "token.txt"
    write_text(new_file, "s3cret", perms=0o600)
    assert stat.S_IMODE(new_file.stat().st_mode) == 0o600

    existing = tmp_path / "existing.conf"
    existing.write_text("old", encoding="utf-8")
    existing.chmod(0o644)
    write_text(existing, "new", perms=0o640)
    assert stat.S_IMODE(existing.stat().st_mode) == 0o640


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_perms_apply_before_any_data_is_written(tmp_path):
    target = tmp_path / "token.txt"
    seen = []

    with atomic_write(target, encoding="utf-8", perms=0o600) as handle:
        tmp_files = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        seen = [stat.S_IMODE(p.stat().st_mode) for p in tmp_files]
        handle.write("s3cret")

    assert seen == [0o600]


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
@pytest.mark.parametrize("special", [0o4755, 0o2755, 0o6755, 0o1777])
def test_setuid_and_setgid_survive(tmp_path, special):
    target = tmp_path / "helper"
    write_text(target, "payload", perms=special)
    assert stat.S_IMODE(target.stat().st_mode) == special


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_existing_setuid_file_keeps_its_bits(tmp_path):
    target = tmp_path / "helper"
    target.write_text("old", encoding="utf-8")
    target.chmod(0o4755)
    write_text(target, "new")
    assert stat.S_IMODE(target.stat().st_mode) == 0o4755


def test_perms_accepts_binary_and_bytes_helpers(tmp_path):
    target = tmp_path / "blob.bin"
    write_bytes(target, b"\x01", perms=0o600)
    if os.name == "posix":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.parametrize("bad", [-1, 0o10000, 99999])
def test_rejects_invalid_perms(tmp_path, bad):
    with pytest.raises(ValueError, match="perms must be"):
        with atomic_write(tmp_path / "f", perms=bad):
            pass


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlinks")
def test_follows_symlink_by_default(tmp_path):
    real = tmp_path / "real.txt"
    real.write_text("old", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(real)

    write_text(link, "new")

    assert link.is_symlink(), "the symlink must not become a regular file"
    assert real.read_text(encoding="utf-8") == "new"


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlinks")
def test_can_replace_symlink_itself(tmp_path):
    real = tmp_path / "real.txt"
    real.write_text("old", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(real)

    write_text(link, "new", follow_symlinks=False)

    assert not link.is_symlink()
    assert real.read_text(encoding="utf-8") == "old"


def test_accepts_str_path(tmp_path):
    target = tmp_path / "as_str.txt"
    write_text(str(target), "ok")
    assert target.read_text(encoding="utf-8") == "ok"


def test_writes_into_current_directory(tmp_path, monkeypatch):
    """A path without a directory must not put the temporary file on another filesystem."""
    monkeypatch.chdir(tmp_path)
    write_text("relative.txt", "ok")
    assert (tmp_path / "relative.txt").read_text(encoding="utf-8") == "ok"


def test_write_bytes_roundtrip(tmp_path):
    target = tmp_path / "blob.bin"
    write_bytes(target, b"\x01\x02")
    assert target.read_bytes() == b"\x01\x02"


def test_durable_false_still_writes(tmp_path):
    target = tmp_path / "fast.txt"
    write_text(target, "fast", durable=False)
    assert target.read_text(encoding="utf-8") == "fast"


def test_durable_true_syncs_file_and_directory(tmp_path, monkeypatch):
    synced = []
    monkeypatch.setattr(core.os, "fsync", lambda fd: synced.append(fd))
    write_text(tmp_path / "durable.txt", "data")
    # Windows has no directory fsync, so only the file itself is flushed there.
    expected = 2 if os.name == "posix" else 1
    assert len(synced) == expected


def test_newline_is_respected(tmp_path):
    target = tmp_path / "crlf.txt"
    with atomic_write(target, encoding="utf-8", newline="\r\n") as handle:
        handle.write("a\nb")
    assert target.read_bytes() == b"a\r\nb"


def test_missing_directory_raises_before_touching_anything(tmp_path):
    with pytest.raises(OSError):
        with atomic_write(tmp_path / "no-such-directory" / "f.txt", encoding="utf-8"):
            pass


_UNPRIVILEGED_POSIX = os.name == "posix" and os.geteuid() != 0


@pytest.mark.skipif(not _UNPRIVILEGED_POSIX, reason="POSIX permissions, non-root only")
def test_readonly_directory_raises_permission_error(tmp_path):
    directory = tmp_path / "ro"
    directory.mkdir()
    directory.chmod(0o500)
    try:
        with pytest.raises(PermissionError):
            write_text(directory / "f.txt", "data")
    finally:
        directory.chmod(0o700)
