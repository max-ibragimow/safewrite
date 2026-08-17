from __future__ import annotations

import io
import os
import stat
import subprocess
import sys

import pytest

from safewrite import __version__
from safewrite.cli import main


def test_writes_stdin_to_file(tmp_path):
    target = tmp_path / "out.txt"
    assert main([str(target)], stdin=io.BytesIO(b"payload")) == 0
    assert target.read_bytes() == b"payload"


def test_reads_input_fully_before_replacing_target(tmp_path):
    target = tmp_path / "app.log"
    target.write_text("DEBUG a\nINFO b\n", encoding="utf-8")

    filtered = b"".join(
        line for line in target.read_bytes().splitlines(keepends=True) if b"DEBUG" not in line
    )
    assert main([str(target)], stdin=io.BytesIO(filtered)) == 0
    assert target.read_text(encoding="utf-8") == "INFO b\n"


def test_append_keeps_previous_content(tmp_path):
    target = tmp_path / "out.txt"
    target.write_text("first line\n", encoding="utf-8")
    assert main([str(target), "--append"], stdin=io.BytesIO(b"second\n")) == 0
    assert target.read_text(encoding="utf-8") == "first line\nsecond\n"


def test_append_on_missing_file_creates_it(tmp_path):
    target = tmp_path / "new.txt"
    assert main([str(target), "-a"], stdin=io.BytesIO(b"data")) == 0
    assert target.read_bytes() == b"data"


def test_no_clobber_refuses_existing_file(tmp_path, capsys):
    target = tmp_path / "out.txt"
    target.write_text("original", encoding="utf-8")
    assert main([str(target), "--no-clobber"], stdin=io.BytesIO(b"new")) == 2
    assert target.read_text(encoding="utf-8") == "original"
    assert "safewrite:" in capsys.readouterr().err


def test_missing_directory_reports_error(tmp_path, capsys):
    assert main([str(tmp_path / "missing" / "f.txt")], stdin=io.BytesIO(b"x")) == 2
    assert "safewrite:" in capsys.readouterr().err


def test_error_message_hides_the_temporary_file(tmp_path, capsys):
    target = tmp_path / "out.txt"
    target.write_text("original", encoding="utf-8")
    assert main([str(target), "--no-clobber"], stdin=io.BytesIO(b"new")) == 2
    err = capsys.readouterr().err
    assert ".tmp" not in err
    # The path must appear verbatim: repr() would double the backslashes on Windows.
    assert str(target) in err
    # "File exists" on POSIX, "Cannot create a file when that file already exists" on Windows.
    assert "exist" in err


def test_refuses_to_truncate_with_empty_input(tmp_path, capsys):
    target = tmp_path / "app.log"
    target.write_text("valuable\n", encoding="utf-8")
    assert main([str(target)], stdin=io.BytesIO(b"")) == 3
    assert target.read_text(encoding="utf-8") == "valuable\n"
    assert "--allow-empty" in capsys.readouterr().err


def test_allow_empty_truncates_on_purpose(tmp_path):
    target = tmp_path / "app.log"
    target.write_text("valuable\n", encoding="utf-8")
    assert main([str(target), "--allow-empty"], stdin=io.BytesIO(b"")) == 0
    assert target.read_bytes() == b""


def test_empty_input_is_fine_for_a_new_file(tmp_path):
    target = tmp_path / "fresh.log"
    assert main([str(target)], stdin=io.BytesIO(b"")) == 0
    assert target.read_bytes() == b""


def test_empty_input_is_fine_with_append(tmp_path):
    target = tmp_path / "app.log"
    target.write_text("kept\n", encoding="utf-8")
    assert main([str(target), "--append"], stdin=io.BytesIO(b"")) == 0
    assert target.read_text(encoding="utf-8") == "kept\n"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_perms_flag(tmp_path):
    target = tmp_path / "token"
    assert main([str(target), "--perms", "600"], stdin=io.BytesIO(b"s3cret")) == 0
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_perms_flag_rejects_garbage(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["/tmp/whatever", "--perms", "not-octal"], stdin=io.BytesIO(b"x"))
    assert exc.value.code == 2
    assert "octal" in capsys.readouterr().err


def test_binary_payload_is_not_mangled(tmp_path):
    target = tmp_path / "blob.bin"
    payload = bytes(range(256))
    assert main([str(target)], stdin=io.BytesIO(payload)) == 0
    assert target.read_bytes() == payload


def test_no_fsync_flag(tmp_path):
    target = tmp_path / "out.txt"
    assert main([str(target), "--no-fsync"], stdin=io.BytesIO(b"fast")) == 0
    assert target.read_bytes() == b"fast"


@pytest.mark.parametrize("flag", ["-V", "--version"])
def test_version_flag(flag, capsys):
    with pytest.raises(SystemExit) as exc:
        main([flag])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_module_entrypoint_reads_real_stdin(tmp_path):
    target = tmp_path / "out.txt"
    subprocess.run(
        [sys.executable, "-m", "safewrite.cli", str(target)],
        input=b"from stdin",
        capture_output=True,
        check=True,
    )
    assert target.read_bytes() == b"from stdin"
