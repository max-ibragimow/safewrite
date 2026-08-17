"""safewrite — atomic file writes with no dependencies."""

from importlib import metadata as _metadata

from .core import atomic_write, write_bytes, write_text

try:
    __version__ = _metadata.version("safewrite")
except _metadata.PackageNotFoundError:  # running from a source tree without installation
    __version__ = "0.0.0.dev0"

__all__ = ["__version__", "atomic_write", "write_bytes", "write_text"]
