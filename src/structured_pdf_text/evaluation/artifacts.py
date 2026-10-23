"""Immutable evaluation artifacts with explicit input identity and publication lock.

An existing namespace can be extended for the same evaluation (e.g. the
other CLI's filename aliases). Previously published bytes are never replaced.
The journal is committed last; an interrupted, unjournaled file fails closed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from structured_pdf_text.atomic_io import atomic_create_text, atomic_write_json

PUBLICATION_PROTOCOL = "evaluation-artifacts-v1"


class ArtifactPublicationError(ValueError):
    """Busy namespace, changed input, unmanaged artifact, or damaged publication."""


def validate_identifier(value: str) -> str:
    """Portable filename components, never paths or Windows special characters."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise ArtifactPublicationError(f"Invalid artifact identifier {value!r}; use letters, digits, '.', '_' or '-'")
    return value


def artifact_namespace(engine: str, run_id: str) -> str:
    validate_identifier(engine)
    validate_identifier(run_id)
    # Matches compute's existing filename convention. Ambiguous joined names
    # and hyphen/underscore aliases intentionally share a lock and journal.
    return f"{engine.replace('-', '_')}_{run_id}"


def reserved_filenames(engine: str, run_id: str) -> set[str]:
    """Known CLI outputs, including aliases used by either entry point."""
    return {f"{prefix}_{name}_{run_id}.{extension}"
            for name in {engine, engine.replace('-', '_')}
            for prefix, extension in (("metrics", "json"), ("metrics_v3", "json"),
                                      ("errors", "md"), ("errors_v3", "md"))}


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def publication_identity(result: dict, *, ref_text: str, hyp_text: str,
                         run_metadata: dict) -> str:
    """Identity of the evaluated snapshots, inputs, policies and run metadata.

    This is stricter than benchmark comparability: hypothesis, full raw source,
    engine, run ID and operational metadata must all remain the same. Paths,
    creation time and implementation commit do not identify a different run.
    """
    identity = {
        "protocol": PUBLICATION_PROTOCOL,
        "schema_version": result["schema_version"],
        "inputs": result["inputs"],
        "evaluator": {key: value for key, value in result["evaluator"].items()
                      if key not in {"created_at_utc", "implementation_commit"}},
        "reference_text_sha256": _digest(ref_text.encode("utf-8")),
        "hypothesis_text_sha256": _digest(hyp_text.encode("utf-8")),
        "run_metadata": run_metadata,
    }
    raw = json.dumps(identity, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return _digest(raw)


@dataclass(frozen=True)
class Artifact:
    role: str
    filename: str
    content: str
    fallback_filename: str | None = None


def _paths(directory: Path, namespace: str) -> tuple[Path, Path]:
    key = _digest(namespace.encode("utf-8"))
    return directory / f".evaluation-{key}.lock", directory / f".evaluation-{key}.json"


@contextmanager
def _publication_lock(path: Path):
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ArtifactPublicationError(
            f"Evaluation publication is busy or interrupted: {path.name}. "
            "Retry after the writer finishes; an interrupted lock needs manual review."
        ) from exc
    try:
        owner = os.fstat(fd)
        os.write(fd, f"pid={os.getpid()}\n".encode("ascii"))
        os.fsync(fd)
        yield
    finally:
        os.close(fd)
        # Never remove a lock replaced by another owner during this operation.
        try:
            current = path.lstat()
            if (current.st_dev, current.st_ino) == (owner.st_dev, owner.st_ino):
                path.unlink()
        except FileNotFoundError:
            pass


def _safe_name(name: str) -> bool:
    return bool(isinstance(name, str) and name not in {"", ".", ".."}
                and not any(char in name for char in '/\\\0<>:"|?*')
                and Path(name).name == name and not name.startswith(".evaluation-"))


def _canonical_payload(content: str) -> dict:
    """Ignore creation provenance, retaining every computed result field."""
    value = json.loads(content)
    if not isinstance(value, dict) or not isinstance(value.get("evaluator"), dict):
        raise ArtifactPublicationError("Invalid canonical metrics artifact")
    value["evaluator"].pop("created_at_utc", None)
    value["evaluator"].pop("implementation_commit", None)
    return value


def _load_journal(path: Path, identity: str) -> dict:
    if not path.exists() and not path.is_symlink():
        return {"protocol": PUBLICATION_PROTOCOL, "identity_sha256": identity, "artifacts": {}}
    try:
        if path.is_symlink():
            raise ValueError("journal is a symlink")
        journal = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(journal, dict) or journal.get("protocol") != PUBLICATION_PROTOCOL
                or not isinstance(journal.get("artifacts"), dict) or not journal["artifacts"]):
            raise ValueError("invalid journal structure or protocol")
        if journal.get("identity_sha256") != identity:
            raise ArtifactPublicationError(
                "Artifact collision: this engine/run-id namespace belongs to different inputs, "
                "policies or run metadata. Use a new run-id or output directory."
            )
        for name, entry in journal["artifacts"].items():
            if (not _safe_name(name) or not isinstance(entry, dict)
                    or not isinstance(entry.get("role"), str) or not entry["role"]
                    or not isinstance(entry.get("sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])):
                raise ValueError("invalid artifact record")
            artifact = path.parent / name
            if artifact.is_symlink() or _digest(artifact.read_bytes()) != entry["sha256"]:
                raise ValueError(f"missing or modified artifact: {name}")
        return journal
    except ArtifactPublicationError:
        raise
    except (OSError, ValueError) as exc:
        raise ArtifactPublicationError(f"Damaged evaluation publication {path.name}: {exc}") from exc


def publish_artifacts(directory: Path, namespace: str, identity: str,
                      artifacts: list[Artifact], *, reserved_names: set[str] | None = None) -> dict[str, Path]:
    """Preflight the entire set, publish only new files, then commit the journal.

    A normal I/O failure rolls back files created by this attempt. Process death
    leaves a lock/incomplete publication for explicit review; no stale-lock
    stealing or repair that could overwrite prior evidence is attempted.
    """
    directory = Path(directory)
    if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity):
        raise ArtifactPublicationError("Publication identity must be a SHA-256 digest")
    if len({artifact.role for artifact in artifacts}) != len(artifacts) or not artifacts:
        raise ArtifactPublicationError("Artifact roles must be nonempty and unique")
    for artifact in artifacts:
        if (not isinstance(artifact.role, str) or not artifact.role
                or not isinstance(artifact.content, str) or not _safe_name(artifact.filename)
                or artifact.fallback_filename is not None and not _safe_name(artifact.fallback_filename)):
            raise ArtifactPublicationError("Invalid artifact filename or role")
    if reserved_names is not None and any(not _safe_name(name) for name in reserved_names):
        raise ArtifactPublicationError("Invalid reserved artifact filename")
    directory.mkdir(parents=True, exist_ok=True)
    lock, journal_path = _paths(directory, namespace)
    with _publication_lock(lock):
        journal = _load_journal(journal_path, identity)
        records = journal["artifacts"]
        for name in reserved_names or ():
            target = directory / name
            if name not in records and (target.exists() or target.is_symlink()):
                raise ArtifactPublicationError(f"Unmanaged artifact already exists: {name}; use a new run-id or directory")
        original_records = dict(records)
        pending: dict[str, str] = {}
        output = {}
        for artifact in artifacts:
            name = artifact.filename
            if name in records and records[name]["role"] != artifact.role:
                name = artifact.fallback_filename
                if name is None:
                    raise ArtifactPublicationError(f"Artifact name is already owned by another role: {artifact.filename}")
            if name in records and records[name]["role"] != artifact.role:
                raise ArtifactPublicationError(f"Artifact fallback is already owned by another role: {name}")
            previous_name = next((key for key, entry in original_records.items()
                                  if entry["role"] == artifact.role), None)
            previous_content = ((directory / previous_name).read_bytes().decode("utf-8")
                                if previous_name else None)
            if artifact.role == "canonical_metrics" and previous_content is not None:
                if _canonical_payload(artifact.content) != _canonical_payload(previous_content):
                    raise ArtifactPublicationError(
                        "Canonical result changed despite identical input identity; "
                        "use a new run-id and investigate determinism or evaluator versioning"
                    )
            target = directory / name
            if name not in records:
                if target.exists() or target.is_symlink():
                    raise ArtifactPublicationError(f"Unmanaged artifact already exists: {name}; use a new run-id or directory")
                # Copy a same-role publication when adding a filename alias.
                content = previous_content if previous_content is not None else artifact.content
                pending[name] = content
                records[name] = {"role": artifact.role, "sha256": _digest(content.encode("utf-8"))}
            output[artifact.role] = target
        written = []
        try:
            for name, content in pending.items():
                atomic_create_text(directory / name, content)
                written.append(name)
            if pending:
                atomic_write_json(journal_path, journal)
        except BaseException:
            # All paths below were absent at preflight and created by us.
            for name in written:
                path = directory / name
                if path.is_file() and not path.is_symlink() and _digest(path.read_bytes()) == records[name]["sha256"]:
                    path.unlink()
            raise
        return output
