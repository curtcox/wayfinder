"""JSON Schema loading and validation."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from wayfinder.core.errors import SchemaValidationError

SCHEMAS_ROOT = Path(__file__).resolve().parents[3] / "schemas"


@lru_cache(maxsize=32)
def _load_schema(relative_path: str) -> dict[str, Any]:
    path = SCHEMAS_ROOT / relative_path
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _registry() -> Registry:
    resources: list[tuple[str, Resource[Any]]] = []
    for path in SCHEMAS_ROOT.rglob("*.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        schema_id = schema.get("$id")
        if isinstance(schema_id, str):
            resources.append((schema_id, Resource.from_contents(schema)))
    return Registry().with_resources(resources)


@lru_cache(maxsize=32)
def validator_for(schema_path: str) -> Draft202012Validator:
    schema = _load_schema(schema_path)
    return Draft202012Validator(schema, registry=_registry())


def validate(instance: Any, schema_path: str) -> None:
    """Validate *instance* against the schema at *schema_path* under schemas/."""
    validator = validator_for(schema_path)
    errors = sorted(validator.iter_errors(instance), key=lambda err: err.path)
    if errors:
        message = "; ".join(error.message for error in errors[:3])
        raise SchemaValidationError(message)
