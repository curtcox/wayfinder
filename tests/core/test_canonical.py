"""Tests for RFC 8785 canonicalization helpers."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from wayfinder.core.canonical import (
    canonical_bytes,
    canonical_compare,
    sha256_digest,
    strip_transport_fields,
)


def test_strip_request_id() -> None:
    obj = {"schema": "wip.goal_create/0.1", "request_id": "req_1", "create_id": "c1"}
    stripped = strip_transport_fields(obj)
    assert "request_id" not in stripped
    assert stripped["create_id"] == "c1"


def test_canonical_compare_ignores_request_id() -> None:
    left = {"a": 1, "request_id": "req_1"}
    right = {"a": 1, "request_id": "req_2"}
    assert canonical_compare(left, right)


_JSON_INT_MAX = 2**53 - 1


@given(
    st.dictionaries(
        st.text(min_size=1),
        st.integers(min_value=-_JSON_INT_MAX, max_value=_JSON_INT_MAX),
    ),
)
def test_canonical_bytes_stable(obj: dict[str, int]) -> None:
    first = canonical_bytes(obj)
    second = canonical_bytes(obj)
    assert first == second


def test_sha256_digest_format() -> None:
    digest = sha256_digest(b"hello")
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64
