"""REST API FastAPI & Fallback Server for E.R.I.I. Engine.

Enables Node.js, Go, Rust, Java, and C# client applications to call E.R.I.I. via HTTP REST API.
Follows Google Python Style Guide.
"""

import argparse
import logging
import sys
from typing import Optional

from erii._version import __version__
from erii.models.archival import (
    ArchivalCapabilityError,
    ArchivalConflictError,
    ArchivalNotFoundError,
    ArchivalProcessingError,
    ArchivalStatus,
    ArchivalSubmissionError,
)
from erii.core.persona_context import PersonaManifestRequiredError
from erii.core.recall import RecallBudgetUnsatisfiedError
from erii.engine import ERIIEngine
from erii.models.adjudication import (
    CandidateConflictError,
    RelationshipEventCandidate as DomainRelationshipEventCandidate,
    SourceTurn as DomainSourceTurn,
)
from erii.models.recall import RecallRequest as DomainRecallRequest
from erii.models.relationship import RelationshipNotFoundError
from erii.models.turn import (
    DeliveryDisposition,
    ReplyAttemptStage,
    SourceProcessingChannel,
    TurnConflictError,
    TurnNotFoundError,
    TurnStatus,
)
from erii.core.temporal_history import TemporalHistoryConflictError

logger = logging.getLogger("erii.server")

_engine: Optional[ERIIEngine] = None


def configure_engine(storage_dir: str = "./erii_memory") -> ERIIEngine:
    """Creates the server engine explicitly for the selected storage directory."""
    global _engine
    if _engine is not None:
        _engine.close()
    _engine = ERIIEngine(storage_dir=storage_dir)
    return _engine


def get_engine() -> ERIIEngine:
    """Returns the lazily initialized server engine."""
    global _engine
    if _engine is None:
        return configure_engine(storage_dir="./erii_memory")
    return _engine


def close_engine() -> None:
    """Closes and clears the server engine when the host shuts down."""
    global _engine
    if _engine is not None:
        _engine.close()
        _engine = None

try:
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel, Field

    app = FastAPI(
        title="E.R.I.I. Memory Engine REST API",
        description="Experiential Recall & Impression Integration Engine",
        version=__version__,
    )

    class RememberRequest(BaseModel):
        agent_id: str = "default_agent"
        user_id: str
        user_message: str
        bot_reply: str

    class RecallRequest(BaseModel):
        agent_id: str = "default_agent"
        user_id: str
        query: str
        top_k: int = 5

    class StructuredRecallBody(DomainRecallRequest):
        """Renderer-neutral structured recall request body."""

    class RelationshipAdjudicationBody(BaseModel):
        """Evidence-backed relationship candidates crossing the REST boundary."""

        agent_id: str = "default_agent"
        user_id: str
        source_turn: DomainSourceTurn
        candidates: list[DomainRelationshipEventCandidate] = Field(
            min_length=1,
            max_length=32,
        )

    class CoreMemoryRequest(BaseModel):
        agent_id: str = "default_agent"
        user_id: str
        content: str

    class ThoughtRequest(BaseModel):
        agent_id: str = "default_agent"
        user_id: str
        content: str
        visibility: str = "public_log"
        is_unresolved: bool = False
        emotional_score: float = 0.0
        foreshadowing_tags: Optional[list] = None
        created_at: Optional[str] = None

    class ResolveThoughtRequest(BaseModel):
        agent_id: str = "default_agent"
        user_id: str

    class ExportRequest(BaseModel):
        agent_id: str = "default_agent"
        user_id: str

    class ImportRequest(BaseModel):
        pack_data: dict
        agent_id: Optional[str] = None
        user_id: Optional[str] = None
        overwrite: bool = False

    class TurnOpeningBody(BaseModel):
        """Opens a durable turn before reply generation."""

        agent_id: str = "default_agent"
        user_id: str
        user_message: str
        turn_id: Optional[str] = None
        interaction_context: list[dict] = Field(default_factory=list)

    class TurnCompletionBody(BaseModel):
        """Seals a visible reply into an existing open turn."""

        agent_id: str = "default_agent"
        user_id: str
        agent_message: str
        continuity_assessment: Optional[dict] = None
        delivery_disposition: DeliveryDisposition = DeliveryDisposition.SHOWN
        processing_channels: Optional[list[SourceProcessingChannel]] = None

    class TurnAbandonmentBody(BaseModel):
        """Explicitly terminates an unanswered turn."""

        agent_id: str = "default_agent"
        user_id: str
        reason: str

    class TurnRecordBody(BaseModel):
        """Atomically records an already-visible complete exchange."""

        agent_id: str = "default_agent"
        user_id: str
        user_message: str
        agent_message: str
        turn_id: Optional[str] = None
        continuity_assessment: Optional[dict] = None
        delivery_disposition: DeliveryDisposition = DeliveryDisposition.SHOWN
        processing_channels: Optional[list[SourceProcessingChannel]] = None

    class ReplyAttemptFailureBody(BaseModel):
        """Sanitized metadata for a failed, undisplayed reply attempt."""

        agent_id: str = "default_agent"
        user_id: str
        attempt_number: int = Field(ge=1)
        stage: ReplyAttemptStage
        capability_descriptor: str
        failure_classification: str

    class ArchivalSubmissionBody(BaseModel):
        """Submits one existing completed Source Turn for reliable archival."""

        agent_id: str = "default_agent"
        user_id: str
        source_turn_id: str
        idempotency_key: str = Field(min_length=1, max_length=256)

    @app.get("/api/v1/health")
    def api_health():
        """Health check endpoint returning engine and version status."""
        return {
            "status": "healthy",
            "version": __version__,
            "engine_initialized": _engine is not None,
            "archiver_running": (
                getattr(_engine.archiver_worker, "running", False)
                if _engine is not None
                else False
            ),
        }

    @app.post("/api/v1/remember")
    def api_remember(req: RememberRequest):
        """Records a conversation turn into memory."""
        try:
            get_engine().remember(
                agent_id=req.agent_id,
                user_id=req.user_id,
                user_message=req.user_message,
                bot_reply=req.bot_reply,
            )
            return {"status": "success", "message": "Turn logged for archival."}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/v1/recall")
    def api_recall(req: RecallRequest):
        """Recalls formatted memory context for prompt injection."""
        try:
            context = get_engine().recall(
                agent_id=req.agent_id,
                user_id=req.user_id,
                query=req.query,
                top_k=req.top_k,
            )
            return {"status": "success", "context": context}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/v1/turns/open", status_code=201)
    def api_begin_turn(req: TurnOpeningBody):
        """Persists one visible user message before reply generation."""
        try:
            turn = get_engine().begin_turn(
                req.agent_id,
                req.user_id,
                req.user_message,
                turn_id=req.turn_id,
                interaction_context=req.interaction_context,
            )
            return {"status": "success", "turn": turn.to_dict()}
        except RelationshipNotFoundError as exc:
            raise HTTPException(status_code=404, detail="turn not found") from exc
        except TurnConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/turns")
    def api_record_turn(req: TurnRecordBody):
        """Atomically accepts an already-visible complete exchange."""
        try:
            receipt = get_engine().record_turn(
                req.agent_id,
                req.user_id,
                req.user_message,
                req.agent_message,
                turn_id=req.turn_id,
                continuity_assessment=req.continuity_assessment,
                delivery_disposition=req.delivery_disposition,
                processing_channels=req.processing_channels,
            )
            return {"status": "success", "receipt": receipt.to_dict()}
        except RelationshipNotFoundError as exc:
            raise HTTPException(status_code=404, detail="turn not found") from exc
        except TurnConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/turns")
    def api_list_turns(
        agent_id: str,
        user_id: str,
        status: Optional[TurnStatus] = None,
    ):
        """Lists relationship-scoped turns in durable opening order."""
        try:
            turns = get_engine().list_turns(
                agent_id,
                user_id,
                status=status,
            )
            return {
                "status": "success",
                "turns": [turn.to_dict() for turn in turns],
            }
        except RelationshipNotFoundError as exc:
            raise HTTPException(status_code=404, detail="turn not found") from exc

    @app.post("/api/v1/turns/{turn_id}/complete")
    def api_complete_turn(turn_id: str, req: TurnCompletionBody):
        """Seals the reply actually displayed by the host."""
        try:
            receipt = get_engine().complete_turn(
                req.agent_id,
                req.user_id,
                turn_id,
                req.agent_message,
                continuity_assessment=req.continuity_assessment,
                delivery_disposition=req.delivery_disposition,
                processing_channels=req.processing_channels,
            )
            return {"status": "success", "receipt": receipt.to_dict()}
        except (RelationshipNotFoundError, TurnNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="turn not found") from exc
        except TurnConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/turns/{turn_id}/reply-attempts", status_code=201)
    def api_record_reply_attempt(turn_id: str, req: ReplyAttemptFailureBody):
        """Records a retryable failure without storing an undisplayed draft."""
        try:
            attempt = get_engine().record_reply_attempt_failure(
                req.agent_id,
                req.user_id,
                turn_id,
                attempt_number=req.attempt_number,
                stage=req.stage,
                capability_descriptor=req.capability_descriptor,
                failure_classification=req.failure_classification,
            )
            return {"status": "success", "attempt": attempt.to_dict()}
        except (RelationshipNotFoundError, TurnNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="turn not found") from exc
        except TurnConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/turns/{turn_id}/reply-attempts")
    def api_list_reply_attempts(turn_id: str, agent_id: str, user_id: str):
        """Lists sanitized failed attempts for one open or terminal turn."""
        try:
            attempts = get_engine().list_reply_attempts(
                agent_id,
                user_id,
                turn_id,
            )
            return {
                "status": "success",
                "attempts": [attempt.to_dict() for attempt in attempts],
            }
        except (RelationshipNotFoundError, TurnNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="turn not found") from exc

    @app.post("/api/v1/turns/{turn_id}/abandon")
    def api_abandon_turn(turn_id: str, req: TurnAbandonmentBody):
        """Explicitly terminates an unanswered turn."""
        try:
            turn = get_engine().abandon_turn(
                req.agent_id,
                req.user_id,
                turn_id,
                reason=req.reason,
            )
            return {"status": "success", "turn": turn.to_dict()}
        except (RelationshipNotFoundError, TurnNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="turn not found") from exc
        except TurnConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/turns/{turn_id}")
    def api_get_turn(turn_id: str, agent_id: str, user_id: str):
        """Returns one relationship-scoped durable turn."""
        try:
            turn = get_engine().get_turn(agent_id, user_id, turn_id)
            return {"status": "success", "turn": turn.to_dict()}
        except (RelationshipNotFoundError, TurnNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="turn not found") from exc

    @app.post("/api/v1/archivals")
    def api_submit_archival(req: ArchivalSubmissionBody):
        """Accepts reliable archival and reports its actual lifecycle state."""
        try:
            receipt = get_engine().archive_turn(
                req.agent_id,
                req.user_id,
                req.source_turn_id,
                idempotency_key=req.idempotency_key,
            )
        except ArchivalCapabilityError as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "archival_capability_unavailable",
                    "retryable": False,
                    "safe_summary": "reliable archival is not configured",
                },
            ) from exc
        except ArchivalConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "archival_conflict",
                    "retryable": False,
                    "safe_summary": (
                        "the archival intent conflicts with an existing binding"
                    ),
                },
            ) from exc
        except (ArchivalSubmissionError, RelationshipNotFoundError) as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_source_turn",
                    "retryable": False,
                    "safe_summary": (
                        "archival requires an existing completed Source Turn"
                    ),
                },
            ) from exc
        except ArchivalProcessingError as exc:
            receipt = exc.receipt
            retryable = bool(receipt.retryable)
            raise HTTPException(
                status_code=503 if retryable else 500,
                detail={
                    "code": (
                        receipt.outcome_code.value
                        if receipt.outcome_code is not None
                        else "archival_processing_failed"
                    ),
                    "retryable": retryable,
                    "safe_summary": receipt.safe_summary,
                    "receipt": receipt.to_dict(),
                },
            ) from exc
        status_code = (
            202
            if receipt.status
            in {
                ArchivalStatus.PENDING,
                ArchivalStatus.PROCESSING,
                ArchivalStatus.RETRY_WAIT,
            }
            else 200
        )
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=status_code,
            content={"receipt": receipt.to_dict()},
            headers={
                "Location": f"/api/v1/archivals/{receipt.archival_id}",
            },
        )

    @app.get("/api/v1/archivals/{archival_id}")
    def api_get_archival(
        archival_id: str,
        agent_id: str,
        user_id: str,
    ):
        """Queries one scoped receipt without exposing its Source Transcript."""
        try:
            receipt = get_engine().get_archival_receipt(
                agent_id,
                user_id,
                archival_id,
            )
            return {"receipt": receipt.to_dict()}
        except ArchivalCapabilityError as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "archival_capability_unavailable",
                    "retryable": False,
                    "safe_summary": "reliable archival is not configured",
                },
            ) from exc
        except (ArchivalNotFoundError, RelationshipNotFoundError) as exc:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "archival_not_found",
                    "retryable": False,
                    "safe_summary": "archival was not found in this scope",
                },
            ) from exc

    @app.post("/api/v1/recall/structured")
    def api_recall_structured(req: StructuredRecallBody):
        """Returns an audience-filtered RecallResult without prompt rendering."""
        try:
            result = get_engine().recall_structured(req)
            return {"status": "success", "result": result.model_dump(mode="json")}
        except PersonaManifestRequiredError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except RecallBudgetUnsatisfiedError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/v1/relationship/adjudicate")
    def api_adjudicate_relationship(req: RelationshipAdjudicationBody):
        """Adjudicates untrusted temporal and relationship candidates with evidence."""
        try:
            result = get_engine().adjudicate_relationship_candidates(
                req.agent_id,
                req.user_id,
                req.source_turn,
                req.candidates,
            )
            return {
                "status": "success",
                "records": [record.to_dict() for record in result.records],
            }
        except (CandidateConflictError, TemporalHistoryConflictError) as e:
            raise HTTPException(status_code=409, detail=str(e))
        except RelationshipNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/v1/core_memory")
    def api_set_core_memory(req: CoreMemoryRequest):
        """Sets Core Persona Memory string."""
        try:
            get_engine().set_core_memory(
                agent_id=req.agent_id, user_id=req.user_id, content=req.content
            )
            return {"status": "success", "message": "Core memory saved."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/v1/core_memory")
    def api_get_core_memory(agent_id: str, user_id: str):
        """Gets Core Persona Memory string."""
        try:
            content = get_engine().get_core_memory(agent_id=agent_id, user_id=user_id)
            return {"status": "success", "content": content}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/v1/memory/monologue")
    def api_get_monologue(
        user_id: str,
        agent_id: str = "default_agent",
        limit: int = 10,
        unresolved_only: bool = False,
        visibility: Optional[str] = "public_log",
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ):
        """Retrieves inner monologue / diary entries."""
        try:
            monologues = get_engine().get_inner_monologue(
                agent_id=agent_id,
                user_id=user_id,
                limit=limit,
                unresolved_only=unresolved_only,
                visibility=visibility,
                start_time=start_time,
                end_time=end_time,
            )
            return {"status": "success", "monologues": monologues}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/v1/memory/thought")
    def api_remember_thought(req: ThoughtRequest):
        """Explicitly records an inner monologue / diary entry."""
        try:
            node = get_engine().remember_thought(
                agent_id=req.agent_id,
                user_id=req.user_id,
                content=req.content,
                visibility=req.visibility,
                is_unresolved=req.is_unresolved,
                emotional_score=req.emotional_score,
                foreshadowing_tags=req.foreshadowing_tags,
                created_at=req.created_at,
            )
            return {"status": "success", "node": node.to_dict()}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.patch("/api/v1/memory/thought/{node_id}/resolve")
    def api_resolve_thought(node_id: str, req: ResolveThoughtRequest):
        """Marks a suspenseful/unresolved thought as resolved."""
        try:
            success = get_engine().resolve_thought(
                agent_id=req.agent_id,
                user_id=req.user_id,
                node_id=node_id,
            )
            if not success:
                raise HTTPException(status_code=404, detail="Thought node not found.")
            return {"status": "success", "message": "Thought resolved successfully."}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/v1/memory/export")
    def api_export_memory(req: ExportRequest):
        """Exports memory into a MemoryPack object."""
        try:
            pack = get_engine().export_memory(agent_id=req.agent_id, user_id=req.user_id)
            return {"status": "success", "pack": pack.to_dict()}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/v1/memory/import")
    def api_import_memory(req: ImportRequest):
        """Imports a MemoryPack object."""
        try:
            pack = get_engine().import_memory(
                pack_or_path=req.pack_data,
                agent_id=req.agent_id,
                user_id=req.user_id,
                overwrite=req.overwrite,
            )
            return {"status": "success", "message": "Memory imported successfully.", "pack": pack.to_dict()}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/v1/tasks/status")
    def api_get_tasks_status():
        """Retrieves background archival task counts by status."""
        try:
            summary = get_engine().archiver_worker.task_queue.get_status_summary()
            return {"status": "success", "summary": summary}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/v1/tasks/retry-failed")
    def api_retry_failed_tasks():
        """Resets FAILED tasks back to PENDING."""
        try:
            count = get_engine().archiver_worker.task_queue.retry_failed()
            return {"status": "success", "reset_count": count}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.on_event("shutdown")
    def api_shutdown() -> None:
        """Releases the lazily initialized engine on server shutdown."""
        close_engine()

except ImportError:
    app = None  # FastAPI not installed


def cli_main():
    """CLI entrypoint for running `erii serve`."""
    parser = argparse.ArgumentParser(description="E.R.I.I. Engine Server CLI")
    parser.add_argument("command", choices=["serve"], help="Command to run")
    parser.add_argument("--host", default="0.0.0.0", help="Host address")
    parser.add_argument("--port", type=int, default=8000, help="Port number")
    parser.add_argument("--storage-dir", default="./erii_memory", help="Memory storage directory")
    args = parser.parse_args()

    if args.command == "serve":
        if app is None:
            print("Error: FastAPI and Uvicorn are required for running REST API server.")
            print("Please install them via: pip install 'erii[server]' or pip install fastapi uvicorn")
            sys.exit(1)

        import uvicorn
        configure_engine(storage_dir=args.storage_dir)
        print(f"Starting E.R.I.I. REST API Server at http://{args.host}:{args.port}")
        try:
            uvicorn.run(app, host=args.host, port=args.port)
        finally:
            close_engine()


if __name__ == "__main__":
    cli_main()
