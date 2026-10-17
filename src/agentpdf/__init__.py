"""Convert large PDF documents into small, searchable files for AI coding agents."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("agentpdf")
except PackageNotFoundError:
    __version__ = "0.1.0"

from .convert import Options, convert
from .search import Hit, build_index, search

__all__ = ["Hit", "Options", "__version__", "build_index", "convert", "search"]
