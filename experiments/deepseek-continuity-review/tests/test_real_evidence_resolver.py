"""Tests for the minimal real-storage evidence boundary."""

from dataclasses import dataclass

import pytest
from erii.models.archival import archival_artifact_fingerprint
from erii.models.continuity_evidence import (
    ContinuityEvidenceKind,
    ContinuityEvidenceRef,
)
from erii.models.node import MemoryNode

from erii_deepseek_continuity import (
    CrossRelationshipLeakError,
    ERIIStorageBackend,
    EvidenceResolutionError,
    FileStorageAdapter,
    RealEvidenceResolver,
    SQLiteStorageAdapter,
    StorageBackend,
)


@dataclass
class _Profile:
    relationship_id: str = "relationship"
    manifest_id: str = "manifest"


@dataclass
class _Claim:
    claim_id: str = "claim"
    statement: str = "Mira records observations before drawing conclusions."


@dataclass
class _Manifest:
    content_fingerprint: str = "1" * 64
    claims: tuple[_Claim, ...] = (_Claim(),)


class _Storage:
    def __init__(self) -> None:
        self.profile = _Profile()
        self.manifest = _Manifest()
        self.node = MemoryNode(
            node_id="node",
            agent_id="agent",
            user_id="user",
            relationship_id="relationship",
            content="Mira and the user repaired a brass telescope.",
        )

    def get_relationship(self, agent_id: str, user_id: str):
        assert (agent_id, user_id) == ("agent", "user")
        return self.profile

    def get_persona_manifest(self, manifest_id: str):
        assert manifest_id == "manifest"
        return self.manifest

    def load_nodes(self, agent_id: str, user_id: str):
        assert (agent_id, user_id) == ("agent", "user")
        return [self.node]


def _resolver() -> tuple[RealEvidenceResolver, _Storage]:
    storage = _Storage()
    backend = ERIIStorageBackend(
        storage,
        agent_id="agent",
        user_id="user",
        relationship_id="relationship",
    )
    return RealEvidenceResolver(backend), storage


def test_public_api_exports_real_adapters() -> None:
    assert ERIIStorageBackend is not None
    assert FileStorageAdapter is not None
    assert SQLiteStorageAdapter is not None
    assert StorageBackend is not None
    assert RealEvidenceResolver is not None


def test_resolves_fingerprint_bound_persona_and_memory() -> None:
    resolver, storage = _resolver()
    persona_ref = ContinuityEvidenceRef.create(
        ContinuityEvidenceKind.PERSONA_CLAIM,
        {
            "manifest_id": "manifest",
            "content_fingerprint": "1" * 64,
            "claim_id": "claim",
        },
    )
    memory_ref = ContinuityEvidenceRef.create(
        ContinuityEvidenceKind.MEMORY_NODE,
        {
            "relationship_id": "relationship",
            "node_id": "node",
            "artifact_fingerprint": archival_artifact_fingerprint(storage.node),
        },
    )

    resolved = resolver.resolve(
        (persona_ref,),
        (memory_ref,),
        relationship_id="relationship",
    )
    assert [item.excerpt for item in resolved] == [
        "Mira records observations before drawing conclusions.",
        "Mira and the user repaired a brass telescope.",
    ]


def test_cross_relationship_and_stale_fingerprint_fail_closed() -> None:
    resolver, _ = _resolver()
    with pytest.raises(CrossRelationshipLeakError, match="relationship_scope_mismatch"):
        resolver.resolve((), (), relationship_id="another-relationship")

    stale_ref = ContinuityEvidenceRef.create(
        ContinuityEvidenceKind.MEMORY_NODE,
        {
            "relationship_id": "relationship",
            "node_id": "node",
            "artifact_fingerprint": "0" * 64,
        },
    )
    with pytest.raises(EvidenceResolutionError, match="memory_fingerprint_mismatch"):
        resolver.resolve((), (stale_ref,), relationship_id="relationship")
