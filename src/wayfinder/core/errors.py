"""Protocol-level errors surfaced by the core library."""

from __future__ import annotations


class InvalidInputError(Exception):
    """Caller supplied invalid or inconsistent input."""


class PolicyDeniedError(Exception):
    """An update was rejected due to authority or policy."""


class StaleRecommendationError(Exception):
    """A recommendation is no longer executable."""


class StorageConflictError(Exception):
    """Concurrent writers or lock/claim conflicts."""


class ArtifactIntegrityError(Exception):
    """Artifact verification failed."""


class SchemaValidationError(Exception):
    """An object failed JSON Schema validation."""
