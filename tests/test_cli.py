"""CLI smoke tests."""

from __future__ import annotations

import pytest

from wayfinder.cli import main


def test_main_capabilities_exit_zero() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["capabilities"])
    assert exc_info.value.code == 0
