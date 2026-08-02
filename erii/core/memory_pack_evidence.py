"""Import-time closure validation for archival artifact evidence."""

from __future__ import annotations

import hashlib
from typing import Iterable, Tuple, Union

from erii.core.evidence_authority import has_exceptional_delivery
from erii.models.archival import (
    ArchivalArtifactKind,
    ArchivalArtifactReference,
    ArchivalOutcomeCode,
    ArchivalStatus,
    ArchivalTombstone,
    TimelineEntry,
    archival_artifact_fingerprint,
)
from erii.models.archival_evidence import ArtifactEvidenceReference
from erii.models.node import MemoryNode
from erii.models.pack import MemoryPack
from erii.models.turn import TurnMessage, TurnRecord, TurnRole, TurnStatus


_SchemaTwoArtifact = Union[MemoryNode, TimelineEntry]


def validate_memory_pack_archival_evidence(pack: MemoryPack) -> None:
    """Proves every schema-2 artifact reference closes over the packed Turns.

    This validation deliberately uses only the MemoryPack snapshot. Import must
    not repair a broken evidence graph by borrowing a Turn from target storage.
    """
    artifacts = tuple(_schema_two_artifacts(pack))
    if not artifacts:
        return
    if pack.relationship is None:
        raise ValueError(
            "MemoryPack schema 2 archival evidence requires a relationship profile"
        )

    relationship_id = pack.relationship.relationship_id
    turns_by_id = {turn.turn_id: turn for turn in pack.turn_records}
    tombstones_by_id = _tombstones_by_id(pack)
    for artifact in artifacts:
        artifact_id = _artifact_id(artifact)
        if not artifact.evidence_references:
            raise ValueError(
                f"MemoryPack schema 2 artifact {artifact_id!r} requires evidence references"
            )
        if artifact.relationship_id != relationship_id:
            raise ValueError(
                f"MemoryPack schema 2 artifact {artifact_id!r} crosses relationship boundaries"
            )
        if artifact.agent_id != pack.agent_id or artifact.user_id != pack.user_id:
            raise ValueError(
                f"MemoryPack schema 2 artifact {artifact_id!r} crosses Agent x User boundaries"
            )

        source_turn_id = artifact.source_turn_id
        turn = turns_by_id.get(source_turn_id)
        if turn is None:
            raise ValueError(
                f"MemoryPack schema 2 artifact {artifact_id!r} references a missing source Turn"
            )
        _validate_source_turn(turn, relationship_id, artifact_id)
        for reference in artifact.evidence_references:
            _validate_reference(reference, turn, artifact_id)
        _validate_portable_archival_commitment(
            artifact,
            turn,
            tombstones_by_id,
            pack,
        )


def _tombstones_by_id(pack: MemoryPack) -> dict:
    by_id = {}
    for tombstone in pack.archival_ledger:
        if tombstone.archival_id in by_id:
            raise ValueError("MemoryPack archival ledger contains duplicate identities")
        by_id[tombstone.archival_id] = tombstone
    return by_id


def _validate_portable_archival_commitment(
    artifact: _SchemaTwoArtifact,
    turn: TurnRecord,
    tombstones_by_id: dict,
    pack: MemoryPack,
) -> None:
    artifact_id = _artifact_id(artifact)
    source_archival_id = artifact.source_archival_id
    tombstone = tombstones_by_id.get(source_archival_id)
    if not isinstance(tombstone, ArchivalTombstone):
        raise ValueError(
            f"MemoryPack schema 2 artifact {artifact_id!r} requires its portable "
            "Archival Tombstone"
        )
    if (
        tombstone.relationship_id != turn.relationship_id
        or tombstone.agent_id != pack.agent_id
        or tombstone.user_id != pack.user_id
        or tombstone.source_turn_id != turn.turn_id
        or tombstone.source_revision != turn.source_revision
        or tombstone.status != ArchivalStatus.COMPLETED
        or tombstone.outcome_code != ArchivalOutcomeCode.ARTIFACTS_COMMITTED
    ):
        raise ValueError(
            f"MemoryPack schema 2 artifact {artifact_id!r} has a mismatched "
            "Archival Tombstone"
        )
    commitments = tombstone.artifact_commitments
    if commitments is None:
        raise ValueError(
            f"MemoryPack schema 2 artifact {artifact_id!r} requires a portable "
            "artifact commitment"
        )
    expected = _artifact_commitment(artifact)
    if expected not in commitments:
        raise ValueError(
            f"MemoryPack schema 2 artifact {artifact_id!r} does not match its "
            "portable artifact commitment"
        )


def _artifact_commitment(
    artifact: _SchemaTwoArtifact,
) -> ArchivalArtifactReference:
    return ArchivalArtifactReference(
        kind=(
            ArchivalArtifactKind.TIMELINE_ENTRY
            if isinstance(artifact, TimelineEntry)
            else ArchivalArtifactKind.MEMORY_NODE
        ),
        artifact_id=_artifact_id(artifact),
        artifact_fingerprint=archival_artifact_fingerprint(artifact),
    )


def _schema_two_artifacts(pack: MemoryPack) -> Iterable[_SchemaTwoArtifact]:
    for artifact in (*pack.nodes, *pack.timeline_entries):
        descriptor = artifact.extractor_descriptor
        if (
            descriptor is not None
            and descriptor.extraction_schema_version == "2"
        ):
            yield artifact


def _artifact_id(artifact: _SchemaTwoArtifact) -> str:
    if isinstance(artifact, TimelineEntry):
        return artifact.timeline_entry_id
    return artifact.node_id


def _validate_source_turn(
    turn: TurnRecord,
    relationship_id: str,
    artifact_id: str,
) -> None:
    if turn.relationship_id != relationship_id:
        raise ValueError(
            f"MemoryPack schema 2 artifact {artifact_id!r} references a source Turn "
            "from another relationship"
        )
    if turn.status != TurnStatus.COMPLETED:
        raise ValueError(
            f"MemoryPack schema 2 artifact {artifact_id!r} requires a completed source Turn"
        )


def _validate_reference(
    reference: ArtifactEvidenceReference,
    turn: TurnRecord,
    artifact_id: str,
) -> None:
    if reference.source_turn_id != turn.turn_id:
        raise ValueError(
            f"MemoryPack schema 2 artifact {artifact_id!r} evidence references "
            "a different source Turn"
        )
    if reference.relationship_id != turn.relationship_id:
        raise ValueError(
            f"MemoryPack schema 2 artifact {artifact_id!r} evidence crosses "
            "relationship boundaries"
        )
    if reference.source_revision != turn.source_revision:
        raise ValueError(
            f"MemoryPack schema 2 artifact {artifact_id!r} evidence source_revision "
            "does not match its source Turn"
        )

    matches = tuple(
        message
        for message in _turn_messages(turn)
        if message.message_id == reference.source_id
    )
    if not matches:
        raise ValueError(
            f"MemoryPack schema 2 artifact {artifact_id!r} evidence source message "
            "was not found"
        )
    if len(matches) != 1:
        raise ValueError(
            f"MemoryPack schema 2 artifact {artifact_id!r} evidence source message "
            "is ambiguous"
        )
    message = matches[0]
    if reference.role != message.role:
        raise ValueError(
            f"MemoryPack schema 2 artifact {artifact_id!r} evidence role does not "
            "match its source message"
        )

    expected_hash = hashlib.sha256(message.content.encode("utf-8")).hexdigest()
    if reference.message_sha256 != expected_hash:
        raise ValueError(
            f"MemoryPack schema 2 artifact {artifact_id!r} evidence hash does not "
            "match its source message"
        )
    if not 0 <= reference.start < reference.end <= len(message.content):
        raise ValueError(
            f"MemoryPack schema 2 artifact {artifact_id!r} evidence span exceeds "
            "its source message"
        )
    if (
        reference.role == TurnRole.AGENT
        and has_exceptional_delivery(turn)
    ):
        raise ValueError(
            "MemoryPack schema 2 artifact Agent evidence is quarantined for an "
            "exceptional delivery"
        )


def _turn_messages(turn: TurnRecord) -> Tuple[TurnMessage, ...]:
    agent_message = turn.transcript.agent_message
    if agent_message is None:
        return (turn.transcript.user_message,)
    return (turn.transcript.user_message, agent_message)
