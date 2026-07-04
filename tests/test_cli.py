"""CLI smoke tests."""

from __future__ import annotations

import pytest

from wayfinder.cli import main


def test_main_exits_with_not_implemented_message(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1
    assert "not yet implemented" in capsys.readouterr().err
