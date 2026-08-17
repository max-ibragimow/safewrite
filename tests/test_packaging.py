"""Checks that the distribution is built and installed correctly."""

from __future__ import annotations

import shutil
import subprocess
from importlib.metadata import entry_points, metadata, version
from pathlib import Path

import pytest

import safewrite


def test_version_matches_distribution_metadata():
    assert safewrite.__version__ == version("safewrite")


def test_metadata_has_release_critical_fields():
    meta = metadata("safewrite")
    assert meta["Name"] == "safewrite"
    assert meta["Summary"]
    assert meta["Requires-Python"]
    assert meta.get_all("Classifier")


def _console_scripts():
    eps = entry_points()
    if hasattr(eps, "select"):
        return list(eps.select(group="console_scripts"))
    return list(eps.get("console_scripts", []))  # pragma: no cover - Python < 3.10


def test_console_script_is_registered():
    scripts = [ep for ep in _console_scripts() if ep.name == "safewrite"]
    assert scripts, "the 'safewrite' console script is not registered"
    assert scripts[0].value == "safewrite.cli:main"


def test_py_typed_marker_is_shipped():
    assert (Path(safewrite.__file__).parent / "py.typed").is_file()


def test_package_has_no_runtime_dependencies():
    assert metadata("safewrite").get_all("Requires-Dist") is None


@pytest.mark.skipif(shutil.which("safewrite") is None, reason="console script is not on PATH")
def test_installed_console_script_works(tmp_path):
    target = tmp_path / "out.txt"
    subprocess.run(["safewrite", str(target)], input=b"ok", check=True)
    assert target.read_bytes() == b"ok"
