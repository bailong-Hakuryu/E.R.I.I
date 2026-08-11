"""Offline, executable integration example for the durable Turn lifecycle.

The example uses a temporary storage directory and a deterministic local
extractor. It performs no network calls and leaves no ``./erii_memory`` or
``./temp_demo`` directory behind.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

from erii import (
    ArchivalNoMemoryDecision,
    DeliveryDisposition,
    ERIIConfig,
    ERIIEngine,
    ExtractorDescriptor,
    RecallAudience,
    RecallRequest,
    ReplyAttemptStage,
    SourceProcessingChannel,
    TurnStatus,
)

AGENT_ID = "agent_lumi"
USER_ID = "user_chen"


class NoMemoryExtractor:
    """Deterministic offline extractor used only by this example."""

    descriptor = ExtractorDescriptor(
        extractor_id="examples.no-memory",
        extractor_version="1.0",
        extraction_schema_version="2",
    )

    def extract(self, _request):
        return ArchivalNoMemoryDecision(reason_code="nothing_durable")


def _delivery_exception(reason_code: str) -> dict[str, object]:
    """Declares why a visible reply was not continuity-reviewed."""
    return {
        "exception_record_version": "delivery-exception-record/v1",
        "disposition": "shown_unreviewed",
        "actor_kind": "host_policy",
        "actor_id": "examples.turn-host/v1",
        "reason_code": reason_code,
        "decided_at": "2026-08-11T00:00:00+00:00",
        "reply_attempt_number": None,
    }


def run_demo(storage_dir: Path) -> dict[str, object]:
    """Runs the complete example and returns values used for verification."""
    with ERIIEngine(
        storage_dir=str(storage_dir),
        memory_extractor=NoMemoryExtractor(),
        config=ERIIConfig(
            storage_dir=str(storage_dir),
            async_archival=False,
        ),
    ) as engine:
        engine.initialize_relationship(
            AGENT_ID,
            USER_ID,
            persona_source=(
                "Lumi is thoughtful and values meaningful conversation."
            ),
            source_format="text/markdown",
            source_name="lumi_persona.md",
        )
        print("[ok] relationship initialized")

        # 1. Persist the exact user-visible input before generating a reply.
        turn = engine.begin_turn(
            AGENT_ID,
            USER_ID,
            "The weather is nice. Shall we take a walk?",
            turn_id="turn-live-001",
        )
        assert turn.status == TurnStatus.OPEN
        print(f"[ok] opened {turn.turn_id}")

        # 2. Recall is side-effect free. The audience is always explicit.
        recall = engine.recall_structured(
            RecallRequest(
                agent_id=AGENT_ID,
                user_id=USER_ID,
                query="walk together",
                audience=RecallAudience.PUBLIC,
            )
        )
        print(f"[ok] recall returned {len(recall.memories)} memory projections")

        # 3. Failed drafts are never persisted; only sanitized failure metadata is.
        attempt = engine.record_reply_attempt_failure(
            AGENT_ID,
            USER_ID,
            turn.turn_id,
            attempt_number=1,
            stage=ReplyAttemptStage.GENERATION,
            capability_descriptor="example-provider/model-v1",
            failure_classification="temporary_provider_error",
        )
        assert attempt.turn_id == turn.turn_id

        # 4. Persist only the reply that the host actually displayed. This demo
        # has no continuity evaluator, so the unreviewed branch is explicit.
        delivery_exception = _delivery_exception("availability_fallback")
        receipt = engine.complete_turn(
            AGENT_ID,
            USER_ID,
            turn.turn_id,
            "Yes. A walk in the park sounds good.",
            delivery_disposition=DeliveryDisposition.SHOWN_UNREVIEWED,
            delivery_exception=delivery_exception,
            processing_channels=[SourceProcessingChannel.MEMORY_ARCHIVAL],
        )
        assert receipt.source_turn_id == turn.turn_id
        print(f"[ok] completed {receipt.source_turn_id}")

        # Identical retries return the same durable completion receipt.
        retried_receipt = engine.complete_turn(
            AGENT_ID,
            USER_ID,
            turn.turn_id,
            "Yes. A walk in the park sounds good.",
            delivery_disposition=DeliveryDisposition.SHOWN_UNREVIEWED,
            delivery_exception=delivery_exception,
            processing_channels=[SourceProcessingChannel.MEMORY_ARCHIVAL],
        )
        assert retried_receipt == receipt

        # 5. Archival has its own required idempotency key and receipt identity.
        archival = engine.archive_turn(
            AGENT_ID,
            USER_ID,
            turn.turn_id,
            idempotency_key="archive-turn-live-001",
        )
        archival_retry = engine.archive_turn(
            AGENT_ID,
            USER_ID,
            turn.turn_id,
            idempotency_key="archive-turn-live-001",
        )
        assert archival_retry.archival_id == archival.archival_id
        print(f"[ok] archival {archival.archival_id} is {archival.status.value}")

        # 6. record_turn is for an exchange that was visible before ingestion.
        historical = engine.record_turn(
            AGENT_ID,
            USER_ID,
            "What is your name?",
            "I am Lumi.",
            turn_id="turn-history-001",
            delivery_disposition=DeliveryDisposition.SHOWN_UNREVIEWED,
            delivery_exception=_delivery_exception(
                "preexisting_visible_exchange"
            ),
            processing_channels=[],
        )
        assert historical.source_turn_id == "turn-history-001"
        print("[ok] recorded one preexisting visible exchange")

        # 7. A generation failure can end without inventing an agent reply.
        engine.begin_turn(
            AGENT_ID,
            USER_ID,
            "This turn will be abandoned.",
            turn_id="turn-abandoned-001",
        )
        abandoned = engine.abandon_turn(
            AGENT_ID,
            USER_ID,
            "turn-abandoned-001",
            reason="generation_failed",
        )
        assert abandoned.status == TurnStatus.ABANDONED
        assert abandoned.transcript.agent_message is None
        print(f"[ok] abandoned {abandoned.turn_id} without an agent message")

        # 8. Reads remain scoped to this exact Agent x User relationship.
        completed = engine.list_turns(
            AGENT_ID,
            USER_ID,
            status=TurnStatus.COMPLETED,
        )
        restored = engine.get_turn(AGENT_ID, USER_ID, turn.turn_id)
        attempts = engine.list_reply_attempts(AGENT_ID, USER_ID, turn.turn_id)
        assert restored.status == TurnStatus.COMPLETED
        assert len(attempts) == 1
        print(f"[ok] listed {len(completed)} completed turns")

        return {
            "turn_id": restored.turn_id,
            "turn_status": restored.status.value,
            "completed_count": len(completed),
            "attempt_count": len(attempts),
            "archival_id": archival.archival_id,
            "archival_status": archival.status.value,
        }


def main() -> None:
    """Uses an auto-cleaned directory so the example never pollutes the CWD."""
    print("E.R.I.I. Turn lifecycle integration example")
    with TemporaryDirectory(prefix="erii-turn-example-") as temporary_dir:
        summary = run_demo(Path(temporary_dir))
    print(
        "[ok] complete: "
        f"turn={summary['turn_id']} archival={summary['archival_status']}"
    )


if __name__ == "__main__":
    main()
