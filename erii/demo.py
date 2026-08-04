"""Self-verifying Golden Continuity Demo orchestration.

The demo deliberately uses only public ``ERIIEngine`` operations, deterministic
host-side extractors, and real SQLite storage. It is an executable proof of the
smallest E.R.I.I. product promise: one relationship remembers a shared
experience and receives only its selected Persona graph across a restart while
another relationship remains isolated.
"""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, Mapping

from erii.data_lifecycle import (
    DataLifecycleCoordinator,
    LifecycleOutcome,
    LifecycleTarget,
    LifecycleTargetKind,
    MemoryPackImportRequest,
)
from erii.models.archival import (
    ArchivalArtifactsDecision,
    ArchivalStatus,
    MemoryCandidate,
    TimelineCandidate,
)
from erii.models.config import ERIIConfig
from erii.models.consolidation import (
    RelationshipProcessingOutcome,
    RelationshipProcessingStatus,
)
from erii.models.node import MemoryType
from erii.models.pack import MemoryPack
from erii.models.provenance import ExtractorDescriptor
from erii.models.recall import (
    PersonaDelivery,
    RecallArtifactProvenance,
    RecallAudience,
    RecallBudget,
    RecallOptions,
    RecallRequest,
)
from erii.engine import ERIIEngine
from erii.storage.sqlite_storage import SQLiteStorage


DEMO_AGENT_ID = "agent-lumi"
DEMO_PRIMARY_USER_ID = "user-a"
DEMO_ISOLATED_USER_ID = "user-b"
DEMO_SOURCE_TURN_ID = "golden-first-snow-turn"
DEMO_REPORT_SCHEMA = "erii.golden-continuity-demo.v2"
DEMO_CHARACTER_PERSONA_SOURCE = (
    "Lumi is an original, curious, and gentle fictional character. She values "
    "experiences that truly happened and respects the independent history of "
    "each relationship."
)
DEMO_PRIMARY_PRIVATE_PERSONA_SOURCE = (
    "Lumi once promised the North Window companion to keep a silver "
    "paper-star sign for their private winter story."
)
DEMO_PERSONA_SOURCE = (
    f"{DEMO_CHARACTER_PERSONA_SOURCE}\n"
    f"{DEMO_PRIMARY_PRIVATE_PERSONA_SOURCE}"
)
DEMO_USER_MESSAGE = "We watched our first snow together."
DEMO_AGENT_REPLY = "Yes. I will remember this quiet snowfall."
DEMO_SHARED_EVENT_SUMMARY = (
    "Lumi and user-a watched their first snow together."
)
DEMO_ARCHIVAL_IDEMPOTENCY_KEY = "golden-first-snow-archive"
DEMO_COMMON_PERSONA_CLAIM_ID = "golden-character-continuity"
DEMO_PRIMARY_PERSONA_CLAIM_ID = "golden-private-paper-star"
DEMO_PRIMARY_PERSONA_SPAN_ID = "golden-private-paper-star-source"
DEMO_PRIMARY_PERSONA_EXPERIENCE_ID = "golden-private-winter-story"
DEMO_PRIMARY_PERSONA_LINK_ID = "golden-private-paper-star-link"
DEMO_PRIMARY_PERSONA_PREMISE_ID = "golden-north-window-premise"
DEMO_PRIMARY_PERSONA_ROLE = "North Window companion"
DEMO_DEFAULT_BASELINE = {
    "familiarity": "minimal",
    "trust": "moderate",
    "intimacy": "minimal",
    "safety": "moderate",
    "conflict_tension": "minimal",
}
DEMO_PRIMARY_PRIVATE_PERSONA_IDS = (
    DEMO_PRIMARY_PERSONA_CLAIM_ID,
    DEMO_PRIMARY_PERSONA_SPAN_ID,
    DEMO_PRIMARY_PERSONA_EXPERIENCE_ID,
    DEMO_PRIMARY_PERSONA_LINK_ID,
    DEMO_PRIMARY_PERSONA_PREMISE_ID,
)


class GoldenContinuityDemoVerificationError(RuntimeError):
    """Raised when a generated demo artifact does not prove its own claims."""


@dataclass(frozen=True)
class GoldenContinuityDemoResult:
    """Paths and verified facts produced by one demo run."""

    output_dir: Path
    report: Mapping[str, Any]

    def summary_lines(self) -> tuple[str, ...]:
        """Returns stable, human-readable CLI output."""
        return (
            "E.R.I.I. Golden Continuity Demo",
            "[PASS] restart persistence",
            "[PASS] relationship isolation",
            "[PASS] provenance",
            "[PASS] portable round trip",
            f"Artifacts: {self.output_dir}",
        )


class _GoldenRelationshipExtractor:
    """Deterministic demo adapter for the host relationship extraction seam."""

    descriptor = ExtractorDescriptor(
        extractor_id="erii.golden-demo.relationship",
        extractor_version="1",
        extraction_schema_version="1",
    )

    def extract(self, request):
        user_message = request.transcript.user_message
        return {
            "kind": "candidates",
            "candidates": [
                {
                    "candidate_key": "first-snow",
                    "event_type": "shared_experience",
                    "summary": DEMO_SHARED_EVENT_SUMMARY,
                    "signal": {
                        "signal_type": "shared_experience",
                        "strength": "moderate",
                        "extraction_confidence": 1.0,
                        "interpretation_confidence": 1.0,
                    },
                    "evidence": [
                        {
                            "source_id": user_message.message_id,
                            "source_revision": request.source_revision,
                            "quote": user_message.content,
                            "start": 0,
                            "end": len(user_message.content),
                        }
                    ],
                    "occurrence_key": "shared:first-snow",
                }
            ],
        }


class _GoldenMemoryExtractor:
    """Deterministic demo adapter for the host memory extraction seam."""

    descriptor = ExtractorDescriptor(
        extractor_id="erii.golden-demo.memory",
        extractor_version="1",
        extraction_schema_version="2",
    )

    def extract(self, request):
        user_message = request.transcript.user_message
        evidence = (
            {
                "citation_version": "archival-evidence-citation/v1",
                "kind": "message_span",
                "source_id": user_message.message_id,
                "source_revision": request.source_revision,
                "quote": user_message.content,
                "start": 0,
                "end": len(user_message.content),
            },
        )
        return ArchivalArtifactsDecision(
            timeline=(
                TimelineCandidate(
                    content=DEMO_SHARED_EVENT_SUMMARY,
                    evidence=evidence,
                ),
            ),
            memories=(
                MemoryCandidate(
                    node_type=MemoryType.EVENT,
                    content=DEMO_SHARED_EVENT_SUMMARY,
                    tags=("first-snow", "shared-experience"),
                    evidence=evidence,
                ),
            ),
        )


def _preexisting_visible_exchange() -> Dict[str, Any]:
    """Declares why a pre-demo exchange may enter the durable turn ledger."""
    return {
        "exception_record_version": "delivery-exception-record/v1",
        "disposition": "shown_unreviewed",
        "actor_kind": "host_policy",
        "actor_id": "erii.golden-demo/v1",
        "reason_code": "preexisting_visible_exchange",
        "decided_at": "2026-08-03T00:00:00+00:00",
        "reply_attempt_number": None,
    }


def _persona_span(span_id: str, quote: str) -> Dict[str, Any]:
    start = DEMO_PERSONA_SOURCE.index(quote)
    return {
        "span_id": span_id,
        "start": start,
        "end": start + len(quote),
        "quote": quote,
    }


def _persona_candidate() -> Dict[str, Any]:
    """Creates a Manifest with common and canonical-relationship meaning."""
    common_span_id = "golden-character-continuity-source"
    return {
        "compiler_version": "erii.golden-demo.persona/v1",
        "source_spans": [
            _persona_span(
                common_span_id,
                DEMO_CHARACTER_PERSONA_SOURCE,
            ),
            _persona_span(
                DEMO_PRIMARY_PERSONA_SPAN_ID,
                DEMO_PRIMARY_PRIVATE_PERSONA_SOURCE,
            ),
        ],
        "claims": [
            {
                "claim_id": DEMO_COMMON_PERSONA_CLAIM_ID,
                "kind": "identity",
                "statement": DEMO_CHARACTER_PERSONA_SOURCE,
                "activation_tier": "foundation",
                "basis": "explicit",
                "scope": "character",
                "source_span_ids": [common_span_id],
            },
            {
                "claim_id": DEMO_PRIMARY_PERSONA_CLAIM_ID,
                "kind": "lore",
                "statement": DEMO_PRIMARY_PRIVATE_PERSONA_SOURCE,
                "activation_tier": "foundation",
                "basis": "explicit",
                "scope": "canonical_relationship",
                "source_span_ids": [DEMO_PRIMARY_PERSONA_SPAN_ID],
                "required_dependency_ids": [DEMO_PRIMARY_PERSONA_LINK_ID],
            },
        ],
        "formative_experiences": [
            {
                "experience_id": DEMO_PRIMARY_PERSONA_EXPERIENCE_ID,
                "title": "The private winter story",
                "summary": DEMO_PRIMARY_PRIVATE_PERSONA_SOURCE,
                "activation_tier": "foundation",
                "scope": "canonical_relationship",
                "source_span_ids": [DEMO_PRIMARY_PERSONA_SPAN_ID],
            },
        ],
        "formative_links": [
            {
                "link_id": DEMO_PRIMARY_PERSONA_LINK_ID,
                "from_id": DEMO_PRIMARY_PERSONA_CLAIM_ID,
                "relation": "relationship_specific",
                "to_id": DEMO_PRIMARY_PERSONA_PREMISE_ID,
                "basis": "explicit",
                "scope": "canonical_relationship",
                "source_span_ids": [DEMO_PRIMARY_PERSONA_SPAN_ID],
            },
        ],
        "premise_templates": [
            {
                "premise_template_id": DEMO_PRIMARY_PERSONA_PREMISE_ID,
                "counterpart_role": DEMO_PRIMARY_PERSONA_ROLE,
                "display_name": "Continue the North Window bond",
                "premise_experience_ids": [
                    DEMO_PRIMARY_PERSONA_EXPERIENCE_ID
                ],
                "qualitative_baseline": DEMO_DEFAULT_BASELINE,
                "source_span_ids": [DEMO_PRIMARY_PERSONA_SPAN_ID],
            },
        ],
    }


def _primary_relationship_premise() -> Dict[str, Any]:
    """Selects the one canonical graph that belongs only to User A."""
    start = DEMO_PERSONA_SOURCE.index(DEMO_PRIMARY_PRIVATE_PERSONA_SOURCE)
    return {
        "premise_id": DEMO_PRIMARY_PERSONA_PREMISE_ID,
        "mode": "canonical_continuation",
        "canonical_role": DEMO_PRIMARY_PERSONA_ROLE,
        "experiences": [
            {
                "experience_id": DEMO_PRIMARY_PERSONA_EXPERIENCE_ID,
                "summary": DEMO_PRIMARY_PRIVATE_PERSONA_SOURCE,
                "source_spans": [
                    {
                        "start": start,
                        "end": start + len(DEMO_PRIMARY_PRIVATE_PERSONA_SOURCE),
                        "quote": DEMO_PRIMARY_PRIVATE_PERSONA_SOURCE,
                    }
                ],
            }
        ],
        "baseline_levels": DEMO_DEFAULT_BASELINE,
    }


def _approve_persona_manifest(
    engine: ERIIEngine,
    user_id: str,
):
    """Approves one deterministic Manifest through the public review seam."""
    proposal = engine.propose_persona_compilation(
        DEMO_AGENT_ID,
        user_id,
        _persona_candidate(),
        created_by="erii.golden-demo/v1",
        proposal_id=f"golden-persona-{user_id}-proposal",
    )
    return engine.decide_persona_compilation(
        DEMO_AGENT_ID,
        user_id,
        proposal.proposal_id,
        proposal.revision,
        "erii.golden-demo/v1",
        "approve",
        reason="Approved synthetic Golden Continuity Demo persona interpretation.",
    )


def _recall(engine: ERIIEngine, user_id: str):
    return engine.recall_structured(
        RecallRequest(
            agent_id=DEMO_AGENT_ID,
            user_id=user_id,
            query="first snow",
            audience=RecallAudience.AGENT_PRIVATE,
            options=RecallOptions(
                top_k=10,
                max_per_type=10,
                persona_delivery=PersonaDelivery.PLANNED,
                budget=RecallBudget(max_cost=50_000),
            ),
        )
    )


def _execute_golden_continuity_demo(
    output_dir: str | Path,
) -> GoldenContinuityDemoResult:
    """Runs the persisted two-user continuity proof in a fresh directory.

    The output directory must not already exist. This avoids silently replacing
    user data and makes every run an independently inspectable artifact.
    """
    root = Path(output_dir).expanduser().resolve()
    database_path = root / "erii-demo.sqlite3"
    memory_pack_path = root / "user-a.erii"
    imported_database_path = root / "user-a-imported.sqlite3"
    recall_path = root / "user-a-recall.md"

    with ERIIEngine(
        storage_driver=SQLiteStorage(str(database_path)),
        config=ERIIConfig(async_archival=False),
        memory_extractor=_GoldenMemoryExtractor(),
        relationship_event_extractor=_GoldenRelationshipExtractor(),
    ) as first_engine:
        primary_profile = first_engine.initialize_relationship(
            DEMO_AGENT_ID,
            DEMO_PRIMARY_USER_ID,
            DEMO_PERSONA_SOURCE,
            relationship_premise=_primary_relationship_premise(),
            source_name="golden-continuity-demo",
        )
        isolated_profile = first_engine.initialize_relationship(
            DEMO_AGENT_ID,
            DEMO_ISOLATED_USER_ID,
            DEMO_PERSONA_SOURCE,
            source_name="golden-continuity-demo",
        )
        primary_manifest = _approve_persona_manifest(
            first_engine,
            DEMO_PRIMARY_USER_ID,
        )
        isolated_manifest = _approve_persona_manifest(
            first_engine,
            DEMO_ISOLATED_USER_ID,
        )
        primary_initial_snapshot = first_engine.get_relationship_snapshot(
            DEMO_AGENT_ID,
            DEMO_PRIMARY_USER_ID,
        )
        isolated_initial_snapshot = first_engine.get_relationship_snapshot(
            DEMO_AGENT_ID,
            DEMO_ISOLATED_USER_ID,
        )
        source_turn = first_engine.record_turn(
            DEMO_AGENT_ID,
            DEMO_PRIMARY_USER_ID,
            DEMO_USER_MESSAGE,
            DEMO_AGENT_REPLY,
            turn_id=DEMO_SOURCE_TURN_ID,
            delivery_exception=_preexisting_visible_exchange(),
        )
        archival_receipt = first_engine.archive_turn(
            DEMO_AGENT_ID,
            DEMO_PRIMARY_USER_ID,
            source_turn.source_turn_id,
            idempotency_key=DEMO_ARCHIVAL_IDEMPOTENCY_KEY,
        )
        processing_run = first_engine.process_relationship_turn(
            DEMO_AGENT_ID,
            DEMO_PRIMARY_USER_ID,
            source_turn.source_turn_id,
        )

    pipeline_ready = (
        archival_receipt.status == ArchivalStatus.COMPLETED
        and processing_run.status == RelationshipProcessingStatus.COMPLETED
        and processing_run.outcome == RelationshipProcessingOutcome.EVENTS_ACCEPTED
        and len(processing_run.event_ids) == 1
    )
    shared_event_id = (
        processing_run.event_ids[0]
        if processing_run.event_ids
        else "missing-shared-event"
    )

    with ERIIEngine(
        storage_driver=SQLiteStorage(str(database_path))
    ) as reopened_engine:
        primary_recall = _recall(reopened_engine, DEMO_PRIMARY_USER_ID)
        isolated_recall = _recall(reopened_engine, DEMO_ISOLATED_USER_ID)
        primary_snapshot = reopened_engine.get_relationship_snapshot(
            DEMO_AGENT_ID,
            DEMO_PRIMARY_USER_ID,
        )
        isolated_snapshot = reopened_engine.get_relationship_snapshot(
            DEMO_AGENT_ID,
            DEMO_ISOLATED_USER_ID,
        )
        reopened_primary_manifest = reopened_engine.get_persona_manifest(
            DEMO_AGENT_ID,
            DEMO_PRIMARY_USER_ID,
        )
        reopened_isolated_manifest = reopened_engine.get_persona_manifest(
            DEMO_AGENT_ID,
            DEMO_ISOLATED_USER_ID,
        )
        reopened_engine.export_memory(
            DEMO_AGENT_ID,
            DEMO_PRIMARY_USER_ID,
            export_path=str(memory_pack_path),
        )
        rendered_recall = reopened_engine.render_recall(primary_recall)

    portable_memory_pack = MemoryPack.from_json(
        memory_pack_path.read_text(encoding="utf-8")
    )
    lifecycle = DataLifecycleCoordinator()
    memory_pack_target = LifecycleTarget(
        LifecycleTargetKind.MEMORY_PACK,
        str(memory_pack_path),
    )
    imported_database_target = LifecycleTarget(
        LifecycleTargetKind.SQLITE,
        str(imported_database_path),
    )
    import_plan = lifecycle.plan(
        MemoryPackImportRequest(
            source=lifecycle.inspect(memory_pack_target),
            destination=imported_database_target,
        )
    )
    import_report = lifecycle.execute(import_plan)

    imported_storage = SQLiteStorage(str(imported_database_path))
    isolated_relationship_absent_after_import = (
        imported_storage.get_relationship(
            DEMO_AGENT_ID,
            DEMO_ISOLATED_USER_ID,
        )
        is None
    )
    with ERIIEngine(storage_driver=imported_storage) as imported_engine:
        imported_recall = _recall(imported_engine, DEMO_PRIMARY_USER_ID)
        imported_snapshot = imported_engine.get_relationship_snapshot(
            DEMO_AGENT_ID,
            DEMO_PRIMARY_USER_ID,
        )
        imported_manifest = imported_engine.get_persona_manifest(
            DEMO_AGENT_ID,
            DEMO_PRIMARY_USER_ID,
        )
        imported_memory_pack = imported_engine.export_memory(
            DEMO_AGENT_ID,
            DEMO_PRIMARY_USER_ID,
        )

    primary_events = {
        event.source_id: event
        for event in primary_recall.events
    }
    isolated_event_ids = {
        event.source_id
        for event in isolated_recall.events
    }
    shared_projection = primary_events.get(shared_event_id)
    linked_memories = [
        memory
        for memory in primary_recall.memories
        if memory.source_kind in {"memory_node", "experiential_timeline"}
    ]
    expected_provenance = {
        ("source_turn", source_turn.source_turn_id, "1"),
        ("archival_batch", archival_receipt.archival_id, None),
    }
    source_chain_complete = len(linked_memories) == 2 and all(
        memory.provenance == RecallArtifactProvenance.SOURCE_LINKED
        and expected_provenance.issubset(
            {
                (
                    reference.source_kind,
                    reference.source_id,
                    reference.source_revision,
                )
                for reference in memory.source_references
            }
        )
        for memory in linked_memories
    )
    imported_portable_memories = [
        memory
        for memory in imported_recall.memories
        if memory.source_kind in {"memory_node", "experiential_timeline"}
    ]
    source_memory_by_id = {
        memory.source_id: memory
        for memory in linked_memories
    }
    imported_memory_by_id = {
        memory.source_id: memory
        for memory in imported_portable_memories
    }
    portable_source_reference = {
        ("source_turn", source_turn.source_turn_id, "1")
    }
    imported_compacted_provenance_valid = (
        len(imported_portable_memories) == 2
        and set(imported_memory_by_id) == set(source_memory_by_id)
        and all(
            imported_memory.provenance
            == RecallArtifactProvenance.PARTIAL_SOURCE
            and imported_memory.authority_tier
            == source_memory_by_id[source_id].authority_tier
            and imported_memory.source_kind
            == source_memory_by_id[source_id].source_kind
            and imported_memory.content
            == source_memory_by_id[source_id].content
            and {
                (
                    reference.source_kind,
                    reference.source_id,
                    reference.source_revision,
                )
                for reference in imported_memory.source_references
            }
            == portable_source_reference
            for source_id, imported_memory in imported_memory_by_id.items()
        )
    )
    primary_persona_context = primary_recall.persona_context
    isolated_persona_context = isolated_recall.persona_context
    primary_persona_items = (
        primary_persona_context.authority_items
        + primary_persona_context.interpretation_items
        + primary_persona_context.approved_growth_items
        if primary_persona_context is not None
        else ()
    )
    isolated_persona_items = (
        isolated_persona_context.authority_items
        + isolated_persona_context.interpretation_items
        + isolated_persona_context.approved_growth_items
        if isolated_persona_context is not None
        else ()
    )
    primary_persona_ids = {
        item.source_id for item in primary_persona_items
    }
    isolated_persona_ids = {
        item.source_id for item in isolated_persona_items
    }
    private_persona_ids = set(DEMO_PRIMARY_PRIVATE_PERSONA_IDS)
    primary_persona_text = "\n".join(
        item.content for item in primary_persona_items
    )
    isolated_persona_text = "\n".join(
        item.content for item in isolated_persona_items
    )
    persona_isolated = (
        reopened_primary_manifest is not None
        and reopened_isolated_manifest is not None
        and reopened_primary_manifest.manifest_id == primary_manifest.manifest_id
        and reopened_isolated_manifest.manifest_id == isolated_manifest.manifest_id
        and reopened_primary_manifest.manifest_id
        != reopened_isolated_manifest.manifest_id
        and primary_persona_context is not None
        and isolated_persona_context is not None
        and primary_persona_context.manifest_id == primary_manifest.manifest_id
        and isolated_persona_context.manifest_id == isolated_manifest.manifest_id
        and private_persona_ids.issubset(primary_persona_ids)
        and private_persona_ids.isdisjoint(isolated_persona_ids)
        and DEMO_COMMON_PERSONA_CLAIM_ID in primary_persona_ids
        and DEMO_COMMON_PERSONA_CLAIM_ID in isolated_persona_ids
        and DEMO_PRIMARY_PRIVATE_PERSONA_SOURCE in primary_persona_text
        and DEMO_PRIMARY_PRIVATE_PERSONA_SOURCE not in isolated_persona_text
    )
    isolated_initial_state = isolated_initial_snapshot.state.to_dict()
    isolated_initial_beliefs = {
        key: belief.to_dict()
        for key, belief in isolated_initial_snapshot.beliefs.items()
    }
    isolated_initial_state_reasons = {
        key: reason.to_dict()
        for key, reason in isolated_initial_snapshot.state_reasons.items()
    }
    isolated_state_unchanged = (
        isolated_snapshot.state.to_dict() == isolated_initial_state
        and {
            key: belief.to_dict()
            for key, belief in isolated_snapshot.beliefs.items()
        }
        == isolated_initial_beliefs
        and {
            key: reason.to_dict()
            for key, reason in isolated_snapshot.state_reasons.items()
        }
        == isolated_initial_state_reasons
    )
    pack_event_ids = {
        event.event_id
        for event in portable_memory_pack.relationship_events
    }
    pack_turn_ids = {
        turn.turn_id
        for turn in portable_memory_pack.turn_records
    }
    pack_manifest_ids = {
        manifest.manifest_id
        for manifest in portable_memory_pack.persona_manifests
    }
    portable_archival_by_id = {
        tombstone.archival_id: tombstone
        for tombstone in portable_memory_pack.archival_ledger
    }
    portable_archival_tombstone = portable_archival_by_id.get(
        archival_receipt.archival_id
    )
    portable_commitment_ids = (
        {
            commitment.artifact_id
            for commitment in portable_archival_tombstone.artifact_commitments
        }
        if portable_archival_tombstone is not None
        and portable_archival_tombstone.artifact_commitments is not None
        else set()
    )
    portable_document = portable_memory_pack.to_dict()
    imported_document = imported_memory_pack.to_dict()
    portable_document["metadata"].pop("exported_at", None)
    imported_document["metadata"].pop("exported_at", None)
    imported_event_ids = {
        event.source_id
        for event in imported_recall.events
    }
    checks: Dict[str, bool] = {
        "restart_persistence": (
            pipeline_ready
            and shared_projection is not None
            and primary_snapshot.event_count == 1
        ),
        "relationship_isolation": (
            shared_event_id not in isolated_event_ids
            and not isolated_recall.memories
            and isolated_snapshot.event_count == 0
            and isolated_state_unchanged
            and primary_snapshot.state.intimacy
            > primary_initial_snapshot.state.intimacy
            and primary_profile.agent_identity_id
            == isolated_profile.agent_identity_id
            and primary_profile.relationship_id
            != isolated_profile.relationship_id
            and primary_profile.persona_id
            != isolated_profile.persona_id
            and persona_isolated
        ),
        "provenance": source_chain_complete,
        "portable_round_trip": (
            memory_pack_path.is_file()
            and imported_database_path.is_file()
            and portable_memory_pack.agent_id == DEMO_AGENT_ID
            and portable_memory_pack.user_id == DEMO_PRIMARY_USER_ID
            and shared_event_id in pack_event_ids
            and source_turn.source_turn_id in pack_turn_ids
            and bool(portable_memory_pack.nodes)
            and bool(portable_memory_pack.timeline_entries)
            and bool(portable_memory_pack.relationship_adjudications)
            and bool(portable_memory_pack.relationship_processing_runs)
            and primary_manifest.manifest_id in pack_manifest_ids
            and isolated_manifest.manifest_id not in pack_manifest_ids
            and import_report.outcome == LifecycleOutcome.APPLIED
            and isolated_relationship_absent_after_import
            and imported_snapshot.profile.relationship_id
            == primary_snapshot.profile.relationship_id
            and imported_snapshot.profile.persona_id
            == primary_snapshot.profile.persona_id
            and imported_snapshot.state.to_dict()
            == primary_snapshot.state.to_dict()
            and shared_event_id in imported_event_ids
            and imported_compacted_provenance_valid
            and set(source_memory_by_id).issubset(portable_commitment_ids)
            and imported_manifest is not None
            and imported_manifest.manifest_id == primary_manifest.manifest_id
            and imported_recall.persona_context is not None
            and imported_recall.persona_context.manifest_id
            == primary_manifest.manifest_id
            and imported_document == portable_document
        ),
    }

    if not all(checks.values()):
        failed = ", ".join(name for name, passed in checks.items() if not passed)
        raise GoldenContinuityDemoVerificationError(
            "Golden Continuity Demo failed verification: "
            f"{failed}; partial artifacts retained at {root}"
        )

    recall_path.write_text(rendered_recall, encoding="utf-8")
    report: Dict[str, Any] = {
        "schema_version": DEMO_REPORT_SCHEMA,
        "status": "passed",
        "agent_id": DEMO_AGENT_ID,
        "primary_user_id": DEMO_PRIMARY_USER_ID,
        "isolated_user_id": DEMO_ISOLATED_USER_ID,
        "source_turn_id": source_turn.source_turn_id,
        "archival_id": archival_receipt.archival_id,
        "shared_event_id": shared_event_id,
        "primary_manifest_id": primary_manifest.manifest_id,
        "isolated_manifest_id": isolated_manifest.manifest_id,
        "common_persona_claim_id": DEMO_COMMON_PERSONA_CLAIM_ID,
        "primary_private_persona_ids": list(
            DEMO_PRIMARY_PRIVATE_PERSONA_IDS
        ),
        "primary_private_persona_phrase": (
            DEMO_PRIMARY_PRIVATE_PERSONA_SOURCE
        ),
        "checks": checks,
        "evidence": {
            "primary_intimacy": primary_snapshot.state.intimacy,
            "isolated_intimacy": isolated_snapshot.state.intimacy,
            "isolated_initial_state": isolated_initial_state,
            "isolated_initial_state_reasons": isolated_initial_state_reasons,
            "import_outcome": import_report.outcome.value,
            "imported_relationship_id": (
                imported_snapshot.profile.relationship_id
            ),
            "source_kind": shared_projection.source_kind,
            "source_references": [
                {
                    "projection_id": memory.projection_id,
                    "references": [
                        reference.model_dump(mode="json")
                        for reference in memory.source_references
                    ],
                }
                for memory in linked_memories
            ],
        },
        "artifacts": {
            "database": database_path.relative_to(root).as_posix(),
            "imported_database": (
                imported_database_path.relative_to(root).as_posix()
            ),
            "memory_pack": memory_pack_path.relative_to(root).as_posix(),
            "recall": recall_path.relative_to(root).as_posix(),
        },
    }
    (root / "demo-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return GoldenContinuityDemoResult(output_dir=root, report=report)


def run_golden_continuity_demo(
    output_dir: str | Path,
) -> GoldenContinuityDemoResult:
    """Runs the Demo and gives every post-creation failure one safe boundary."""
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=False)
    try:
        return _execute_golden_continuity_demo(root)
    except GoldenContinuityDemoVerificationError:
        raise
    except Exception as exc:
        raise GoldenContinuityDemoVerificationError(
            "Golden Continuity Demo failed before verification completed: "
            f"{exc}; partial artifacts retained at {root}"
        ) from exc
