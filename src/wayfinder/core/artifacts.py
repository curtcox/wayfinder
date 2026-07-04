"""Content-addressed artifact storage (§2.2, §6.8)."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from wayfinder.core.canonical import sha256_digest
from wayfinder.core.errors import ArtifactIntegrityError


@dataclass(frozen=True)
class StoredArtifact:
    """Result of writing bytes into the artifact store."""

    artifact_id: str
    uri: str
    sha256: str
    bytes: int
    path: Path


class ArtifactStore:
    """Goal-scoped content-addressed artifact store."""

    def __init__(self, artifacts_root: Path, *, goal_id: str) -> None:
        self.root = artifacts_root.resolve()
        self.goal_id = goal_id
        self.sha_root = self.root / "sha256"
        self.sha_root.mkdir(parents=True, exist_ok=True)

    @classmethod
    def for_goal(cls, store_root: Path, goal_id: str) -> ArtifactStore:
        return cls(store_root / "goals" / goal_id / "artifacts", goal_id=goal_id)

    def write_bytes(
        self,
        content: bytes,
        *,
        artifact_id: str,
        media_type: str = "application/octet-stream",
        description: str | None = None,
    ) -> dict[str, Any]:
        """Write content using temp-write→fsync→verify→rename (§2.2)."""
        digest = sha256_digest(content)
        hex_digest = digest.removeprefix("sha256:")
        rel_path = Path("sha256") / hex_digest[:2] / hex_digest
        final_path = self._resolve_contained(self.root / rel_path)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        if final_path.exists():
            existing = final_path.read_bytes()
            if sha256_digest(existing) != digest:
                msg = "content address collision with mismatched bytes"
                raise ArtifactIntegrityError(msg)
        else:
            self._atomic_write(final_path, content)
            if sha256_digest(final_path.read_bytes()) != digest:
                msg = "artifact digest verification failed after write"
                raise ArtifactIntegrityError(msg)
        uri = f"file:.wayfinder/goals/{self.goal_id}/artifacts/{rel_path.as_posix()}"
        ref: dict[str, Any] = {
            "schema": "wip.artifact/0.1",
            "protocol_version": "0.1",
            "artifact_id": artifact_id,
            "uri": uri,
            "media_type": media_type,
            "sha256": digest,
            "bytes": len(content),
            "redacted": False,
        }
        if description is not None:
            ref["description"] = description
        return ref

    def verify_reference(self, artifact: dict[str, Any]) -> None:
        """Verify digest and existence for an artifact reference."""
        path = self.resolve_uri(str(artifact["uri"]))
        if not path.exists():
            msg = f"artifact missing: {artifact.get('artifact_id')}"
            raise ArtifactIntegrityError(msg)
        content = path.read_bytes()
        if sha256_digest(content) != str(artifact["sha256"]):
            msg = f"artifact digest mismatch: {artifact.get('artifact_id')}"
            raise ArtifactIntegrityError(msg)

    def resolve_uri(self, uri: str) -> Path:
        """Resolve a relative file: artifact URI inside this goal's artifact root."""
        parsed = urlparse(uri)
        if parsed.scheme != "file":
            msg = f"unsupported artifact URI scheme: {parsed.scheme}"
            raise ArtifactIntegrityError(msg)
        raw_path = unquote(parsed.path)
        if parsed.netloc:
            raw_path = f"/{parsed.netloc}{raw_path}"
        if raw_path.startswith("/"):
            msg = "absolute file URIs are rejected by default"
            raise ArtifactIntegrityError(msg)
        marker = f".wayfinder/goals/{self.goal_id}/artifacts/"
        if marker not in raw_path:
            msg = "artifact URI escapes goal artifact root"
            raise ArtifactIntegrityError(msg)
        suffix = raw_path.split(marker, 1)[1]
        if ".." in Path(suffix).parts:
            msg = "artifact URI contains parent segments"
            raise ArtifactIntegrityError(msg)
        return self._resolve_contained(self.root / suffix)

    def _resolve_contained(self, path: Path) -> Path:
        resolved = path.resolve()
        root = self.root.resolve()
        if root not in resolved.parents and resolved != root:
            msg = "artifact path escapes store root"
            raise ArtifactIntegrityError(msg)
        return resolved

    def _atomic_write(self, final_path: Path, content: bytes) -> None:
        final_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(dir=final_path.parent, prefix=".artifact-", suffix=".tmp")
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            digest = hashlib.sha256(content).hexdigest()
            if digest != final_path.name:
                msg = "artifact temp digest mismatch"
                raise ArtifactIntegrityError(msg)
            temp_path.replace(final_path)
            final_path.chmod(0o600)
            dir_fd = os.open(final_path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            temp_path.unlink(missing_ok=True)
