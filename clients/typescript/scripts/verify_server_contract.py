"""Verify that the TypeScript SDK still matches the live FastAPI contract."""

from __future__ import annotations

import importlib
from collections.abc import Mapping

from fastapi.testclient import TestClient


def _request_schema(operation: Mapping[str, object]) -> str | None:
    body = operation.get("requestBody")
    if not isinstance(body, Mapping):
        return None
    content = body.get("content")
    if not isinstance(content, Mapping):
        return None
    media = content.get("application/json")
    if not isinstance(media, Mapping):
        return None
    schema = media.get("schema")
    if not isinstance(schema, Mapping):
        return None
    reference = schema.get("$ref")
    return str(reference).rsplit("/", maxsplit=1)[-1] if reference else None


def main() -> None:
    server = importlib.import_module("erii.server.app")
    specification = server.app.openapi()
    paths = specification["paths"]
    expected_operations = {
        ("/api/v1/health", "get"): None,
        ("/api/v1/recall", "post"): "RecallRequest",
        ("/api/v1/core_memory", "post"): "CoreMemoryRequest",
        ("/api/v1/core_memory", "get"): None,
        ("/api/v1/turns/open", "post"): "TurnOpeningBody",
        ("/api/v1/turns", "post"): "TurnRecordBody",
        ("/api/v1/turns", "get"): None,
        ("/api/v1/turns/{turn_id}", "get"): None,
        ("/api/v1/turns/{turn_id}/complete", "post"): "TurnCompletionBody",
        ("/api/v1/turns/{turn_id}/abandon", "post"): "TurnAbandonmentBody",
        (
            "/api/v1/turns/{turn_id}/continuity/evaluate",
            "post",
        ): "ContinuityEvaluationBody",
        (
            "/api/v1/turns/{turn_id}/reply-attempts",
            "post",
        ): "ReplyAttemptFailureBody",
        ("/api/v1/turns/{turn_id}/reply-attempts", "get"): None,
        ("/api/v1/archivals", "post"): "ArchivalSubmissionBody",
        ("/api/v1/archivals/{archival_id}", "get"): None,
        ("/api/v1/memory/export", "post"): "ExportRequest",
        ("/api/v1/memory/import", "post"): "ImportRequest",
    }
    for (path, method), schema_name in expected_operations.items():
        assert path in paths, f"server route disappeared: {path}"
        operation = paths[path].get(method)
        assert operation is not None, f"server method disappeared: {method.upper()} {path}"
        assert _request_schema(operation) == schema_name, (
            f"request schema changed for {method.upper()} {path}: "
            f"{_request_schema(operation)!r}"
        )

    for obsolete_path in ("/health", "/api/v1/export", "/api/v1/import"):
        assert obsolete_path not in paths, f"obsolete SDK route became ambiguous: {obsolete_path}"

    assert specification["security"] == [{"OwnerApiKey": []}]
    assert paths["/api/v1/health"]["get"]["security"] == []
    protected_operation = paths["/api/v1/turns/open"]["post"]
    assert "security" not in protected_operation
    error_reference = "#/components/schemas/RESTErrorEnvelope"
    assert (
        protected_operation["responses"]["422"]["content"]
        ["application/json"]["schema"]["$ref"]
        == error_reference
    )
    assert (
        protected_operation["responses"]["default"]["content"]
        ["application/json"]["schema"]["$ref"]
        == error_reference
    )

    schemas = specification["components"]["schemas"]
    expected_required_fields = {
        "TurnOpeningBody": {"user_id", "user_message"},
        "TurnRecordBody": {
            "user_id",
            "user_message",
            "agent_message",
            "delivery_exception",
        },
        "TurnCompletionBody": {"user_id", "agent_message"},
        "TurnAbandonmentBody": {"user_id", "reason"},
        "ContinuityEvaluationBody": {
            "user_id",
            "proposed_reply",
            "persona_context_refs",
        },
        "ReplyAttemptFailureBody": {
            "user_id",
            "attempt_number",
            "stage",
            "capability_descriptor",
            "failure_classification",
        },
        "ArchivalSubmissionBody": {
            "user_id",
            "source_turn_id",
            "idempotency_key",
        },
        "ImportRequest": {"pack_data"},
    }
    for schema_name, required_fields in expected_required_fields.items():
        actual = set(schemas[schema_name].get("required", []))
        assert required_fields <= actual, (
            f"required fields changed for {schema_name}: "
            f"expected at least {sorted(required_fields)}, got {sorted(actual)}"
        )

    assert schemas["DeliveryDisposition"]["enum"] == [
        "shown",
        "overridden",
        "shown_unreviewed",
    ]
    assert schemas["SourceProcessingChannel"]["enum"] == [
        "memory_archival",
        "relationship_adjudication",
    ]
    assert schemas["ReplyAttemptStage"]["enum"] == [
        "generation",
        "continuity_evaluation",
        "delivery_preparation",
    ]

    api_key = "-".join(["typescript", "contract"] * 4)
    server.configure_server_access(api_key)
    try:
        with TestClient(server.app) as client:
            assert client.get("/api/v1/health").status_code == 200

            unauthenticated = client.post("/api/v1/recall", json={})
            assert unauthenticated.status_code == 401
            assert unauthenticated.json()["detail"]["code"] == "authentication_required"

            bearer = client.post(
                "/api/v1/recall",
                json={},
                headers={"Authorization": f"Bearer {api_key}"},
            )
            assert bearer.status_code == 401

            api_key_request = client.post(
                "/api/v1/recall",
                json={},
                headers={"X-API-Key": api_key},
            )
            assert api_key_request.status_code == 422
    finally:
        server.configure_server_access(None)

    print("TypeScript SDK/FastAPI contract: OK")


if __name__ == "__main__":
    main()
