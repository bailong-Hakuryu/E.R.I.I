"""REST API FastAPI & Fallback Server for E.R.I.I. Engine.

Enables Node.js, Go, Rust, Java, and C# client applications to call E.R.I.I. via HTTP REST API.
Follows Google Python Style Guide.
"""

import argparse
from contextlib import asynccontextmanager
import hashlib
import ipaddress
import logging
import os
import secrets
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
from erii.core.continuity import ContinuityEvaluationCapabilityError
from erii.core.recall import RecallBudgetUnsatisfiedError
from erii.engine import ERIIEngine
from erii.models.adjudication import (
    CandidateConflictError,
    RelationshipEventCandidate as DomainRelationshipEventCandidate,
)
from erii.models.continuity import ContinuityEvaluationResult
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
_api_key_digest: Optional[bytes] = None
_allow_unauthenticated_loopback = False
MAX_REST_REQUEST_BODY_BYTES = 8 * 1024 * 1024
MAX_REST_IMPORT_COLLECTION_ITEMS = 10_000
MAX_REST_IMPORT_TOTAL_ITEMS = 25_000
_MEMORY_PACK_COLLECTION_FIELDS = (
    "nodes",
    "timeline",
    "timeline_entries",
    "archival_ledger",
    "relationship_events",
    "relationship_direct_event_ids",
    "relationship_adjudications",
    "persona_growth_proposals",
    "persona_compilation_proposals",
    "persona_manifests",
    "turn_records",
    "relationship_processing_runs",
    "persona_reflection_decisions",
    "relationship_consequences",
    "narrative_tension_links",
)


def configure_server_access(
    api_key: Optional[str],
    *,
    allow_unauthenticated_loopback: bool = False,
) -> None:
    """Configures the reference server's single-owner access boundary."""
    global _api_key_digest, _allow_unauthenticated_loopback
    if api_key is None:
        _api_key_digest = None
        _allow_unauthenticated_loopback = bool(
            allow_unauthenticated_loopback
        )
        return
    if not isinstance(api_key, str) or len(api_key.encode("utf-8")) < 32:
        raise ValueError("ERII_API_KEY must contain at least 32 UTF-8 bytes")
    _api_key_digest = hashlib.sha256(api_key.encode("utf-8")).digest()
    _allow_unauthenticated_loopback = False


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
    from fastapi import FastAPI, HTTPException, Query, Request
    from fastapi.exceptions import RequestValidationError
    from fastapi.openapi.utils import get_openapi
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, ConfigDict, Field, model_validator
    from starlette.exceptions import HTTPException as StarletteHTTPException

    _ERROR_CONTRACTS = {
        "archival_capability_unavailable": (
            503,
            False,
            "Reliable archival is not configured.",
        ),
        "archival_conflict": (
            409,
            False,
            "The archival request conflicts with an existing binding.",
        ),
        "archival_not_found": (
            404,
            False,
            "Archival was not found in this relationship scope.",
        ),
        "authentication_required": (
            401,
            False,
            "A valid service API key is required.",
        ),
        "continuity_capability_unavailable": (
            503,
            False,
            "Continuity evaluation is not configured.",
        ),
        "internal_error": (
            500,
            False,
            "The server could not complete the request.",
        ),
        "invalid_memory_pack": (
            422,
            False,
            "MemoryPack failed validation.",
        ),
        "invalid_request": (
            400,
            False,
            "The request could not be accepted.",
        ),
        "invalid_source_turn": (
            422,
            False,
            "Archival requires an existing completed Source Turn.",
        ),
        "loopback_access_required": (
            403,
            False,
            "Unauthenticated development access is restricted to loopback.",
        ),
        "persona_manifest_required": (
            409,
            False,
            "An approved Persona Manifest is required for this operation.",
        ),
        "recall_budget_unsatisfied": (
            422,
            False,
            "The recall request cannot be satisfied within its declared budget.",
        ),
        "relationship_conflict": (
            409,
            False,
            "Relationship state conflicts with the requested operation.",
        ),
        "relationship_not_found": (
            404,
            False,
            "Relationship is not initialized.",
        ),
        "request_too_large": (
            413,
            False,
            "Request body exceeds the server limit.",
        ),
        "route_not_found": (404, False, "Route not found."),
        "server_access_unconfigured": (
            503,
            False,
            "Reference-server access has not been configured.",
        ),
        "thought_not_found": (404, False, "Thought node was not found."),
        "turn_conflict": (
            409,
            False,
            "Turn state conflicts with the requested operation.",
        ),
        "turn_not_found": (
            404,
            False,
            "Turn was not found in this relationship scope.",
        ),
        "validation_error": (
            422,
            False,
            "Request validation failed.",
        ),
    }

    def _error_detail(
        code: str,
        *,
        safe_summary: Optional[str] = None,
        retryable: Optional[bool] = None,
        extra: Optional[dict] = None,
    ) -> dict:
        """Builds the single public REST error representation."""
        contract = _ERROR_CONTRACTS.get(code)
        if contract is None:
            default_retryable = False
            default_summary = "The request could not be completed."
        else:
            _, default_retryable, default_summary = contract
        detail = {
            "code": code,
            "retryable": (
                default_retryable if retryable is None else bool(retryable)
            ),
            "safe_summary": safe_summary or default_summary,
        }
        if extra:
            detail.update(
                {
                    key: value
                    for key, value in extra.items()
                    if key not in detail
                }
            )
        return detail

    def _error_response(
        code: str,
        *,
        safe_summary: Optional[str] = None,
        retryable: Optional[bool] = None,
        extra: Optional[dict] = None,
        headers: Optional[dict[str, str]] = None,
        status_code: Optional[int] = None,
    ) -> JSONResponse:
        """Returns the canonical error envelope for middleware and handlers."""
        contract = _ERROR_CONTRACTS.get(code)
        resolved_status = (
            status_code
            if status_code is not None
            else (contract[0] if contract is not None else 500)
        )
        return JSONResponse(
            status_code=resolved_status,
            content={
                "detail": _error_detail(
                    code,
                    safe_summary=safe_summary,
                    retryable=retryable,
                    extra=extra,
                )
            },
            headers=headers,
        )

    class _RequestBodyTooLarge(Exception):
        """Internal signal raised before FastAPI parses an oversized body."""

    class _RequestBodyLimitMiddleware:
        """Rejects declared and streamed request bodies above a fixed byte cap."""

        def __init__(self, asgi_app, max_bytes: int) -> None:
            self.app = asgi_app
            self.max_bytes = max_bytes

        async def _reject(self, scope, receive, send) -> None:
            response = _error_response("request_too_large")
            await response(scope, receive, send)

        async def __call__(self, scope, receive, send) -> None:
            if scope.get("type") != "http":
                await self.app(scope, receive, send)
                return

            headers = {
                key.lower(): value
                for key, value in scope.get("headers", ())
            }
            raw_length = headers.get(b"content-length")
            if raw_length is not None:
                try:
                    declared_length = int(raw_length)
                except (TypeError, ValueError):
                    declared_length = 0
                if declared_length > self.max_bytes:
                    await self._reject(scope, receive, send)
                    return

            received_bytes = 0

            async def limited_receive():
                nonlocal received_bytes
                message = await receive()
                if message.get("type") == "http.request":
                    received_bytes += len(message.get("body", b""))
                    if received_bytes > self.max_bytes:
                        raise _RequestBodyTooLarge
                return message

            try:
                await self.app(scope, limited_receive, send)
            except _RequestBodyTooLarge:
                await self._reject(scope, receive, send)

    class _ReferenceAccessMiddleware:
        """Enforces one owner key or an explicit loopback-only development mode."""

        def __init__(self, asgi_app) -> None:
            self.app = asgi_app

        @staticmethod
        async def _reject(scope, receive, send, code: str) -> None:
            response = _error_response(
                code,
                headers=(
                    {"WWW-Authenticate": "APIKey"}
                    if code == "authentication_required"
                    else None
                ),
            )
            await response(scope, receive, send)

        async def __call__(self, scope, receive, send) -> None:
            public_paths = {
                "/api/v1/health",
                "/docs",
                "/docs/oauth2-redirect",
                "/openapi.json",
            }
            if (
                scope.get("type") != "http"
                or scope.get("path") in public_paths
            ):
                await self.app(scope, receive, send)
                return

            expected_digest = _api_key_digest
            if expected_digest is None:
                if not _allow_unauthenticated_loopback:
                    await self._reject(
                        scope,
                        receive,
                        send,
                        "server_access_unconfigured",
                    )
                    return
                client = scope.get("client")
                client_host = str(client[0]) if client else ""
                if not _is_loopback_host(client_host):
                    await self._reject(
                        scope,
                        receive,
                        send,
                        "loopback_access_required",
                    )
                    return
                await self.app(scope, receive, send)
                return

            supplied_values = [
                value
                for key, value in scope.get("headers", ())
                if key.lower() == b"x-api-key"
            ]
            supplied = supplied_values[0] if len(supplied_values) == 1 else b""
            supplied_digest = hashlib.sha256(supplied).digest()
            if not secrets.compare_digest(supplied_digest, expected_digest):
                await self._reject(
                    scope,
                    receive,
                    send,
                    "authentication_required",
                )
                return
            await self.app(scope, receive, send)

    @asynccontextmanager
    async def _reference_lifespan(_app: FastAPI):
        """Closes lazily created engine resources at ASGI shutdown."""
        try:
            yield
        finally:
            close_engine()

    app = FastAPI(
        title="E.R.I.I. Memory Engine REST API",
        description="Experiential Recall & Impression Integration Engine",
        version=__version__,
        lifespan=_reference_lifespan,
    )
    app.add_middleware(
        _RequestBodyLimitMiddleware,
        max_bytes=MAX_REST_REQUEST_BODY_BYTES,
    )
    app.add_middleware(_ReferenceAccessMiddleware)

    def _reference_openapi():
        """Documents the owner API key used by the enforcement middleware."""
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        components = schema.setdefault("components", {})
        components.setdefault("securitySchemes", {})["OwnerApiKey"] = {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
            "description": (
                "Single-owner reference-server key; not a tenant identity."
            ),
        }
        components.setdefault("schemas", {})["RESTErrorDetail"] = {
            "type": "object",
            "additionalProperties": True,
            "required": ["code", "retryable", "safe_summary"],
            "properties": {
                "code": {"type": "string"},
                "retryable": {"type": "boolean"},
                "safe_summary": {"type": "string"},
            },
        }
        components["schemas"]["RESTErrorEnvelope"] = {
            "type": "object",
            "additionalProperties": False,
            "required": ["detail"],
            "properties": {
                "detail": {
                    "$ref": "#/components/schemas/RESTErrorDetail",
                }
            },
        }

        error_content = {
            "application/json": {
                "schema": {
                    "$ref": "#/components/schemas/RESTErrorEnvelope",
                }
            }
        }
        public_operation_paths = {"/", "/api/v1/health"}
        for path, path_item in schema.get("paths", {}).items():
            if not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if method.lower() not in {
                    "get",
                    "post",
                    "put",
                    "patch",
                    "delete",
                    "options",
                    "head",
                    "trace",
                } or not isinstance(operation, dict):
                    continue
                if path in public_operation_paths:
                    # An empty operation-level requirement overrides the global
                    # owner-key requirement for the two middleware-public routes.
                    operation["security"] = []
                responses = operation.setdefault("responses", {})
                validation_response = responses.get("422")
                if isinstance(validation_response, dict):
                    validation_response["content"] = error_content
                    validation_response["description"] = (
                        "Canonical request validation error."
                    )
                responses.setdefault(
                    "default",
                    {
                        "description": "Canonical REST error response.",
                        "content": error_content,
                    },
                )
        schema["security"] = [{"OwnerApiKey": []}]
        app.openapi_schema = schema
        return schema

    app.openapi = _reference_openapi

    @app.exception_handler(RequestValidationError)
    async def _request_validation_error_handler(
        _request: Request,
        _exc: RequestValidationError,
    ) -> JSONResponse:
        """Hides parser internals behind the stable validation contract."""
        return _error_response("validation_error")

    @app.exception_handler(StarletteHTTPException)
    async def _http_error_handler(
        _request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        """Normalizes framework 404s and endpoint-raised HTTP errors."""
        if isinstance(exc.detail, dict):
            code = exc.detail.get("code")
            summary = exc.detail.get("safe_summary")
            if isinstance(code, str) and code:
                known = _ERROR_CONTRACTS.get(code)
                extra = {
                    key: value
                    for key, value in exc.detail.items()
                    if key not in {"code", "retryable", "safe_summary"}
                }
                return _error_response(
                    code,
                    safe_summary=(
                        summary
                        if (
                            known is None
                            and isinstance(summary, str)
                            and summary.strip()
                        )
                        else None
                    ),
                    retryable=(
                        None
                        if known is not None
                        else bool(exc.detail.get("retryable", False))
                    ),
                    extra=extra,
                    headers=exc.headers,
                    status_code=(None if known is not None else exc.status_code),
                )
        if exc.status_code == 404:
            return _error_response("route_not_found", headers=exc.headers)
        if exc.status_code == 422:
            return _error_response("validation_error", headers=exc.headers)
        return _error_response(
            "http_error",
            headers=exc.headers,
            status_code=exc.status_code,
        )

    @app.exception_handler(RelationshipNotFoundError)
    async def _relationship_not_found_handler(
        _request: Request,
        _exc: RelationshipNotFoundError,
    ) -> JSONResponse:
        return _error_response("relationship_not_found")

    @app.exception_handler(TurnNotFoundError)
    async def _turn_not_found_handler(
        _request: Request,
        _exc: TurnNotFoundError,
    ) -> JSONResponse:
        return _error_response("turn_not_found")

    @app.exception_handler(TurnConflictError)
    async def _turn_conflict_handler(
        _request: Request,
        _exc: TurnConflictError,
    ) -> JSONResponse:
        return _error_response("turn_conflict")

    @app.exception_handler(ContinuityEvaluationCapabilityError)
    async def _continuity_capability_handler(
        _request: Request,
        _exc: ContinuityEvaluationCapabilityError,
    ) -> JSONResponse:
        return _error_response("continuity_capability_unavailable")

    @app.exception_handler(PersonaManifestRequiredError)
    async def _persona_manifest_required_handler(
        _request: Request,
        _exc: PersonaManifestRequiredError,
    ) -> JSONResponse:
        return _error_response("persona_manifest_required")

    @app.exception_handler(RecallBudgetUnsatisfiedError)
    async def _recall_budget_handler(
        _request: Request,
        _exc: RecallBudgetUnsatisfiedError,
    ) -> JSONResponse:
        return _error_response("recall_budget_unsatisfied")

    @app.exception_handler(ArchivalCapabilityError)
    async def _archival_capability_handler(
        _request: Request,
        _exc: ArchivalCapabilityError,
    ) -> JSONResponse:
        return _error_response("archival_capability_unavailable")

    @app.exception_handler(ArchivalConflictError)
    async def _archival_conflict_handler(
        _request: Request,
        _exc: ArchivalConflictError,
    ) -> JSONResponse:
        return _error_response("archival_conflict")

    @app.exception_handler(ArchivalNotFoundError)
    async def _archival_not_found_handler(
        _request: Request,
        _exc: ArchivalNotFoundError,
    ) -> JSONResponse:
        return _error_response("archival_not_found")

    @app.exception_handler(ArchivalSubmissionError)
    async def _archival_submission_handler(
        _request: Request,
        _exc: ArchivalSubmissionError,
    ) -> JSONResponse:
        return _error_response("invalid_source_turn")

    @app.exception_handler(ArchivalProcessingError)
    async def _archival_processing_handler(
        _request: Request,
        exc: ArchivalProcessingError,
    ) -> JSONResponse:
        receipt = exc.receipt
        retryable = bool(receipt.retryable)
        return _error_response(
            (
                receipt.outcome_code.value
                if receipt.outcome_code is not None
                else "archival_processing_failed"
            ),
            safe_summary=receipt.safe_summary,
            retryable=retryable,
            extra={"receipt": receipt.to_dict()},
            status_code=503 if retryable else 500,
        )

    @app.exception_handler(CandidateConflictError)
    @app.exception_handler(TemporalHistoryConflictError)
    async def _relationship_conflict_handler(
        _request: Request,
        _exc: Exception,
    ) -> JSONResponse:
        return _error_response("relationship_conflict")

    @app.exception_handler(ValueError)
    async def _domain_validation_error_handler(
        _request: Request,
        _exc: ValueError,
    ) -> JSONResponse:
        return _error_response("validation_error")

    @app.exception_handler(Exception)
    async def _unexpected_error_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        logger.error(
            "Unhandled REST error during %s %s (%s)",
            request.method,
            request.url.path,
            type(exc).__name__,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return _error_response("internal_error")

    def _internal_server_error(
        operation: str,
        exc: Exception,
    ) -> HTTPException:
        """Logs diagnostics server-side and returns a stable public error."""
        logger.error(
            "Unhandled REST error during %s (%s)",
            operation,
            type(exc).__name__,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        contract = _ERROR_CONTRACTS["internal_error"]
        return HTTPException(
            status_code=contract[0],
            detail=_error_detail("internal_error"),
        )

    def _standard_error(
        code: str,
    ) -> HTTPException:
        """Creates an endpoint error from the canonical contract registry."""
        contract = _ERROR_CONTRACTS.get(code)
        if contract is None:
            raise ValueError(f"unknown REST error code: {code}")
        return HTTPException(
            status_code=contract[0],
            detail=_error_detail(code),
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
        top_k: int = Field(default=5, ge=1, le=100)

    class StructuredRecallBody(DomainRecallRequest):
        """Renderer-neutral structured recall request body."""

    class RelationshipAdjudicationBody(BaseModel):
        """Candidates bound to a canonical persisted Source Turn."""

        model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

        agent_id: str = "default_agent"
        user_id: str
        source_turn_id: str = Field(min_length=1, max_length=256)
        extractor_version: str = Field(min_length=1, max_length=128)
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

        @model_validator(mode="after")
        def memory_pack_collections_are_bounded(self) -> "ImportRequest":
            total_items = 0
            for field_name in _MEMORY_PACK_COLLECTION_FIELDS:
                value = self.pack_data.get(field_name, [])
                if not isinstance(value, list):
                    continue
                if len(value) > MAX_REST_IMPORT_COLLECTION_ITEMS:
                    raise ValueError(
                        f"MemoryPack {field_name} exceeds the REST import limit"
                    )
                total_items += len(value)
            if total_items > MAX_REST_IMPORT_TOTAL_ITEMS:
                raise ValueError("MemoryPack exceeds the total REST import item limit")
            return self

    class RelationshipConsequenceBody(BaseModel):
        """Records a relationship consequence from an adjudicated event."""

        model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

        agent_id: str = "default_agent"
        user_id: str
        source_turn_id: str = Field(min_length=1, max_length=256)
        source_decision_id: str = Field(min_length=1, max_length=256)
        source_event_id: str = Field(min_length=1, max_length=256)
        effects: list[str] = Field(min_length=1, max_length=16)
        summary: str = Field(min_length=1, max_length=2048)
        recorded_at: Optional[str] = None

    class NarrativeTensionLinkBody(BaseModel):
        """Records a narrative tension link from a later adjudicated event."""

        model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

        agent_id: str = "default_agent"
        user_id: str
        consequence_id: str = Field(min_length=1, max_length=256)
        source_turn_id: str = Field(min_length=1, max_length=256)
        source_decision_id: str = Field(min_length=1, max_length=256)
        source_event_id: str = Field(min_length=1, max_length=256)
        outcome: str = Field(min_length=1, max_length=64)
        summary: str = Field(min_length=1, max_length=2048)
        recorded_at: Optional[str] = None

    class TurnOpeningBody(BaseModel):
        """Opens a durable turn before reply generation."""

        agent_id: str = "default_agent"
        user_id: str
        user_message: str
        turn_id: Optional[str] = None
        interaction_context: list[dict] = Field(default_factory=list)

    class TurnCompletionBody(BaseModel):
        """Seals a visible reply into an existing open turn."""

        model_config = ConfigDict(extra="forbid")

        agent_id: str = "default_agent"
        user_id: str
        agent_message: str
        continuity_assessment: Optional[dict] = None
        continuity_result: Optional[dict] = None
        delivery_disposition: DeliveryDisposition = (
            DeliveryDisposition.SHOWN_UNREVIEWED
        )
        delivery_exception: Optional[dict] = None
        processing_channels: Optional[list[SourceProcessingChannel]] = None

        @model_validator(mode="after")
        def delivery_branch_is_explicit(self) -> "TurnCompletionBody":
            if self.continuity_result is not None:
                if self.continuity_assessment is not None:
                    raise ValueError(
                        "continuity_result and continuity_assessment are mutually exclusive"
                    )
                if self.delivery_disposition == DeliveryDisposition.SHOWN:
                    if self.delivery_exception is not None:
                        raise ValueError(
                            "ordinary shown reviewed delivery cannot have an exception"
                        )
                    return self
                if self.delivery_disposition == DeliveryDisposition.OVERRIDDEN:
                    if self.delivery_exception is None:
                        raise ValueError(
                            "overridden reviewed delivery requires delivery_exception"
                        )
                    return self
                raise ValueError(
                    "reviewed REST completion must use shown or overridden"
                )
            if self.delivery_disposition != DeliveryDisposition.SHOWN_UNREVIEWED:
                raise ValueError(
                    "REST completion without a full continuity result must use "
                    "shown_unreviewed"
                )
            if self.delivery_exception is None:
                raise ValueError(
                    "shown_unreviewed completion requires delivery_exception"
                )
            return self

    class ContinuityEvaluationBody(BaseModel):
        """Evaluates one unpersisted reply against an already-open Turn."""

        model_config = ConfigDict(extra="forbid")

        agent_id: str = "default_agent"
        user_id: str
        proposed_reply: str
        persona_context_refs: list[dict[str, object]]
        relationship_context_refs: list[dict[str, object]] = Field(
            default_factory=list
        )

    class TurnAbandonmentBody(BaseModel):
        """Explicitly terminates an unanswered turn."""

        agent_id: str = "default_agent"
        user_id: str
        reason: str

    class TurnRecordBody(BaseModel):
        """Atomically records an already-visible complete exchange."""

        model_config = ConfigDict(extra="forbid")

        agent_id: str = "default_agent"
        user_id: str
        user_message: str
        agent_message: str
        turn_id: Optional[str] = None
        delivery_disposition: DeliveryDisposition = (
            DeliveryDisposition.SHOWN_UNREVIEWED
        )
        delivery_exception: dict
        processing_channels: Optional[list[SourceProcessingChannel]] = None

        @model_validator(mode="after")
        def preexisting_delivery_is_explicit(self) -> "TurnRecordBody":
            if self.delivery_disposition != DeliveryDisposition.SHOWN_UNREVIEWED:
                raise ValueError("recorded exchanges must use shown_unreviewed")
            return self

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
        except ValueError:
            raise _standard_error("invalid_request")
        except Exception as e:
            raise _internal_server_error("remember", e) from e

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
        except ValueError:
            raise _standard_error("invalid_request")
        except Exception as e:
            raise _internal_server_error("recall", e) from e

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
        except RelationshipNotFoundError:
            raise _standard_error("relationship_not_found")
        except TurnConflictError:
            raise _standard_error("turn_conflict")
        except ValueError:
            raise _standard_error("validation_error")

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
                delivery_exception=req.delivery_exception,
                delivery_disposition=req.delivery_disposition,
                processing_channels=req.processing_channels,
            )
            return {"status": "success", "receipt": receipt.to_dict()}
        except RelationshipNotFoundError:
            raise _standard_error("relationship_not_found")
        except TurnConflictError:
            raise _standard_error("turn_conflict")
        except ValueError:
            raise _standard_error("validation_error")

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
        except RelationshipNotFoundError:
            raise _standard_error("relationship_not_found")

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
                continuity_result=(
                    ContinuityEvaluationResult.from_dict(req.continuity_result)
                    if req.continuity_result is not None
                    else None
                ),
                delivery_exception=req.delivery_exception,
                delivery_disposition=req.delivery_disposition,
                processing_channels=req.processing_channels,
            )
            return {"status": "success", "receipt": receipt.to_dict()}
        except (RelationshipNotFoundError, TurnNotFoundError):
            raise _standard_error("turn_not_found")
        except TurnConflictError:
            raise _standard_error("turn_conflict")
        except ValueError:
            raise _standard_error("validation_error")

    @app.post("/api/v1/turns/{turn_id}/continuity/evaluate")
    def api_evaluate_turn_continuity(
        turn_id: str,
        req: ContinuityEvaluationBody,
    ):
        """Returns a strict self-bound result without persisting the draft reply."""
        try:
            result = get_engine().evaluate_reply_continuity(
                req.agent_id,
                req.user_id,
                turn_id,
                req.proposed_reply,
                persona_context_refs=req.persona_context_refs,
                relationship_context_refs=req.relationship_context_refs,
            )
            return {"status": "success", "result": result.to_dict()}
        except (RelationshipNotFoundError, TurnNotFoundError):
            raise _standard_error("turn_not_found")
        except TurnConflictError:
            raise _standard_error("turn_conflict")
        except ContinuityEvaluationCapabilityError:
            raise _standard_error("continuity_capability_unavailable")
        except PersonaManifestRequiredError:
            raise _standard_error("persona_manifest_required")
        except ValueError:
            raise _standard_error("validation_error")

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
        except (RelationshipNotFoundError, TurnNotFoundError):
            raise _standard_error("turn_not_found")
        except TurnConflictError:
            raise _standard_error("turn_conflict")
        except ValueError:
            raise _standard_error("validation_error")

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
        except (RelationshipNotFoundError, TurnNotFoundError):
            raise _standard_error("turn_not_found")

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
        except (RelationshipNotFoundError, TurnNotFoundError):
            raise _standard_error("turn_not_found")
        except TurnConflictError:
            raise _standard_error("turn_conflict")
        except ValueError:
            raise _standard_error("validation_error")

    @app.get("/api/v1/turns/{turn_id}")
    def api_get_turn(turn_id: str, agent_id: str, user_id: str):
        """Returns one relationship-scoped durable turn."""
        try:
            turn = get_engine().get_turn(agent_id, user_id, turn_id)
            return {"status": "success", "turn": turn.to_dict()}
        except (RelationshipNotFoundError, TurnNotFoundError):
            raise _standard_error("turn_not_found")

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
            raise _standard_error("archival_capability_unavailable") from exc
        except ArchivalConflictError as exc:
            raise _standard_error("archival_conflict") from exc
        except (ArchivalSubmissionError, RelationshipNotFoundError) as exc:
            raise _standard_error("invalid_source_turn") from exc
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
            raise _standard_error("archival_capability_unavailable") from exc
        except (ArchivalNotFoundError, RelationshipNotFoundError) as exc:
            raise _standard_error("archival_not_found") from exc

    @app.post("/api/v1/recall/structured")
    def api_recall_structured(req: StructuredRecallBody):
        """Returns an audience-filtered RecallResult without prompt rendering."""
        try:
            result = get_engine().recall_structured(req)
            return {"status": "success", "result": result.model_dump(mode="json")}
        except PersonaManifestRequiredError:
            raise _standard_error("persona_manifest_required")
        except RecallBudgetUnsatisfiedError:
            raise _standard_error("recall_budget_unsatisfied")
        except ValueError:
            raise _standard_error("invalid_request")
        except Exception as e:
            raise _internal_server_error("structured_recall", e) from e

    @app.post("/api/v1/relationship/adjudicate")
    def api_adjudicate_relationship(req: RelationshipAdjudicationBody):
        """Adjudicates candidates against a persisted completed Source Turn."""
        try:
            result = get_engine().adjudicate_turn_candidates(
                req.agent_id,
                req.user_id,
                req.source_turn_id,
                req.candidates,
                extractor_version=req.extractor_version,
            )
            return {
                "status": "success",
                "records": [record.to_dict() for record in result.records],
            }
        except (CandidateConflictError, TemporalHistoryConflictError):
            raise _standard_error("relationship_conflict")
        except RelationshipNotFoundError:
            raise _standard_error("relationship_not_found")
        except TurnNotFoundError:
            raise _standard_error("turn_not_found")
        except ValueError:
            raise _standard_error("invalid_request")
        except Exception as e:
            raise _internal_server_error("relationship_adjudication", e) from e

    @app.post("/api/v1/relationship/consequences")
    def api_record_relationship_consequence(req: RelationshipConsequenceBody):
        """Records a relationship consequence from an adjudicated event."""
        try:
            consequence = get_engine().record_relationship_consequence(
                req.agent_id,
                req.user_id,
                req.source_turn_id,
                req.source_decision_id,
                req.source_event_id,
                tuple(req.effects),
                req.summary,
                recorded_at=req.recorded_at,
            )
            return {
                "status": "success",
                "consequence": consequence.to_dict(),
            }
        except RelationshipNotFoundError:
            raise _standard_error("relationship_not_found")
        except TurnNotFoundError:
            raise _standard_error("turn_not_found")
        except ValueError:
            raise _standard_error("invalid_request")
        except Exception as e:
            raise _internal_server_error("record_relationship_consequence", e) from e

    @app.get("/api/v1/relationship/consequences")
    def api_list_relationship_consequences(
        agent_id: str = "default_agent",
        user_id: str = Query(...),
    ):
        """Lists all relationship consequences for the given relationship."""
        try:
            relationship = get_engine().storage.get_relationship(agent_id, user_id)
            if relationship is None:
                raise RelationshipNotFoundError(
                    f"Relationship not found: {agent_id}, {user_id}"
                )
            consequences = get_engine().storage.list_relationship_consequences(
                relationship.relationship_id
            )
            return {
                "status": "success",
                "consequences": [item.to_dict() for item in consequences],
            }
        except RelationshipNotFoundError:
            raise _standard_error("relationship_not_found")
        except Exception as e:
            raise _internal_server_error("list_relationship_consequences", e) from e

    @app.post("/api/v1/relationship/narrative-tension-links")
    def api_record_narrative_tension_link(req: NarrativeTensionLinkBody):
        """Records a narrative tension link from a later adjudicated event."""
        try:
            link = get_engine().record_narrative_tension_link(
                req.agent_id,
                req.user_id,
                req.consequence_id,
                req.source_turn_id,
                req.source_decision_id,
                req.source_event_id,
                req.outcome,
                req.summary,
                recorded_at=req.recorded_at,
            )
            return {
                "status": "success",
                "link": link.to_dict(),
            }
        except RelationshipNotFoundError:
            raise _standard_error("relationship_not_found")
        except TurnNotFoundError:
            raise _standard_error("turn_not_found")
        except ValueError:
            raise _standard_error("invalid_request")
        except Exception as e:
            raise _internal_server_error("record_narrative_tension_link", e) from e

    @app.get("/api/v1/relationship/narrative-tension-links")
    def api_list_narrative_tension_links(
        agent_id: str = "default_agent",
        user_id: str = Query(...),
    ):
        """Lists all narrative tension links for the given relationship."""
        try:
            relationship = get_engine().storage.get_relationship(agent_id, user_id)
            if relationship is None:
                raise RelationshipNotFoundError(
                    f"Relationship not found: {agent_id}, {user_id}"
                )
            links = get_engine().storage.list_narrative_tension_links(
                relationship.relationship_id
            )
            return {
                "status": "success",
                "links": [item.to_dict() for item in links],
            }
        except RelationshipNotFoundError:
            raise _standard_error("relationship_not_found")
        except Exception as e:
            raise _internal_server_error("list_narrative_tension_links", e) from e

    @app.post("/api/v1/core_memory")
    def api_set_core_memory(req: CoreMemoryRequest):
        """Sets Core Persona Memory string."""
        try:
            get_engine().set_core_memory(
                agent_id=req.agent_id, user_id=req.user_id, content=req.content
            )
            return {"status": "success", "message": "Core memory saved."}
        except Exception as e:
            raise _internal_server_error("set_core_memory", e) from e

    @app.get("/api/v1/core_memory")
    def api_get_core_memory(agent_id: str, user_id: str):
        """Gets Core Persona Memory string."""
        try:
            content = get_engine().get_core_memory(agent_id=agent_id, user_id=user_id)
            return {"status": "success", "content": content}
        except Exception as e:
            raise _internal_server_error("get_core_memory", e) from e

    @app.get("/api/v1/memory/monologue")
    def api_get_monologue(
        user_id: str,
        agent_id: str = "default_agent",
        limit: int = Query(default=10, ge=1, le=100),
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
            raise _internal_server_error("get_monologue", e) from e

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
            raise _internal_server_error("remember_thought", e) from e

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
                raise _standard_error("thought_not_found")
            return {"status": "success", "message": "Thought resolved successfully."}
        except HTTPException:
            raise
        except Exception as e:
            raise _internal_server_error("resolve_thought", e) from e

    @app.post("/api/v1/memory/export")
    def api_export_memory(req: ExportRequest):
        """Exports memory into a MemoryPack object."""
        try:
            pack = get_engine().export_memory(agent_id=req.agent_id, user_id=req.user_id)
            return {"status": "success", "pack": pack.to_dict()}
        except Exception as e:
            raise _internal_server_error("export_memory", e) from e

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
        except ValueError as exc:
            raise _standard_error("invalid_memory_pack") from exc
        except Exception as e:
            raise _internal_server_error("import_memory", e) from e

    @app.get("/api/v1/tasks/status")
    def api_get_tasks_status():
        """Retrieves background archival task counts by status."""
        try:
            summary = get_engine().archiver_worker.task_queue.get_status_summary()
            return {"status": "success", "summary": summary}
        except Exception as e:
            raise _internal_server_error("task_status", e) from e

    @app.post("/api/v1/tasks/retry-failed")
    def api_retry_failed_tasks():
        """Resets FAILED tasks back to PENDING."""
        try:
            count = get_engine().archiver_worker.task_queue.retry_failed()
            return {"status": "success", "reset_count": count}
        except Exception as e:
            raise _internal_server_error("retry_failed_tasks", e) from e

except ImportError:
    app = None  # FastAPI not installed


def _is_loopback_host(host: str) -> bool:
    """Returns whether a bind host is unambiguously local-only."""
    normalized = host.strip().casefold()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def cli_main():
    """CLI entrypoint for the reference server and continuity demo."""
    parser = argparse.ArgumentParser(description="E.R.I.I. command line interface")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Run the reference REST server")
    serve_parser.add_argument("--host", default="127.0.0.1", help="Host address")
    serve_parser.add_argument("--port", type=int, default=8000, help="Port number")
    serve_parser.add_argument(
        "--storage-dir",
        default="./erii_memory",
        help="Memory storage directory",
    )
    serve_parser.add_argument(
        "--allow-unsafe-network",
        action="store_true",
        help=(
            "Explicitly allow a non-loopback bind with an owner API key but "
            "without built-in TLS or user-level authorization"
        ),
    )
    serve_parser.add_argument(
        "--allow-unauthenticated-loopback",
        action="store_true",
        help=(
            "Explicitly allow local-only development requests without "
            "ERII_API_KEY"
        ),
    )
    demo_parser = subparsers.add_parser(
        "demo",
        help="Run the self-verifying Golden Continuity Demo",
    )
    demo_parser.add_argument(
        "--output-dir",
        default="./erii-demo",
        help="Fresh directory for storage, recall, report, and MemoryPack artifacts",
    )
    args = parser.parse_args()

    if args.command == "demo":
        from erii.demo import (
            GoldenContinuityDemoVerificationError,
            run_golden_continuity_demo,
        )

        try:
            result = run_golden_continuity_demo(args.output_dir)
        except FileExistsError as exc:
            parser.error(str(exc))
        except GoldenContinuityDemoVerificationError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        for line in result.summary_lines():
            print(line)
        return

    if args.command == "serve":
        if app is None:
            print("Error: FastAPI and Uvicorn are required for running REST API server.")
            print("Please install them via: pip install 'erii[server]' or pip install fastapi uvicorn")
            sys.exit(1)

        is_loopback = _is_loopback_host(args.host)
        if not is_loopback and not args.allow_unsafe_network:
            parser.error(
                "non-loopback binds require --allow-unsafe-network because "
                "the reference server has no built-in TLS or user authorization"
            )
        if not is_loopback and args.allow_unauthenticated_loopback:
            parser.error(
                "--allow-unauthenticated-loopback cannot be used with a "
                "non-loopback host"
            )

        api_key = os.environ.get("ERII_API_KEY") or None
        if api_key is not None:
            try:
                configure_server_access(api_key)
            except ValueError as exc:
                parser.error(str(exc))
        elif is_loopback and args.allow_unauthenticated_loopback:
            configure_server_access(
                None,
                allow_unauthenticated_loopback=True,
            )
        else:
            parser.error(
                "set ERII_API_KEY to at least 32 bytes, or use "
                "--allow-unauthenticated-loopback for explicit local-only "
                "development"
            )

        if not is_loopback:
            warning = (
                "WARNING: the E.R.I.I. reference server uses one owner-level "
                "API key and plain HTTP; terminate TLS and enforce user "
                "authorization at a trusted proxy."
            )
            logger.warning(warning)
            print(warning)

        import uvicorn
        configure_engine(storage_dir=args.storage_dir)
        print(f"Starting E.R.I.I. REST API Server at http://{args.host}:{args.port}")
        try:
            uvicorn.run(app, host=args.host, port=args.port)
        finally:
            close_engine()


if __name__ == "__main__":
    cli_main()
