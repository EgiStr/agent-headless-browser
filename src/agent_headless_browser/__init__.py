"""Headless Chrome for AI agents that stays signed in to your own Google account."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("agent-headless-browser")
except PackageNotFoundError:  # running from a source checkout without install
    __version__ = "0.0.0"

__all__ = ["__version__"]
