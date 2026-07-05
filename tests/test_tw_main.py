"""wayfinder-tw main module tests."""

from __future__ import annotations

import pytest

from wayfinder.tw import main as tw_main


def test_tw_main_maps_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(tw_main, "run_cli", boom)
    with pytest.raises(SystemExit) as exc:
        tw_main.main([])
    assert exc.value.code != 0
