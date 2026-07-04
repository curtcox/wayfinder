"""Smoke tests for the package skeleton."""

from wayfinder import __version__


def test_version_is_set() -> None:
    assert __version__ == "0.1.0"
