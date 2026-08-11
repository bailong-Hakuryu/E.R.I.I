"""Public response contracts for reference-server errors."""

import importlib
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from erii import ERIIEngine, FileStorage

server_module = importlib.import_module("erii.server.app")
TEST_API_KEY = "".join(("test-rest-errors-", "x" * 32))


def _error(code: str, summary: str) -> dict[str, object]:
    return {
        "detail": {
            "code": code,
            "retryable": False,
            "safe_summary": summary,
        }
    }


class RestErrorContractPublicTests(unittest.TestCase):
    def setUp(self):
        self.engine = ERIIEngine(
            storage_driver=FileStorage(tempfile.mkdtemp())
        )
        self.engine.initialize_relationship(
            "agent_erii",
            "user_one",
            "A quiet character who values honest companionship.",
        )
        server_module._engine = self.engine
        server_module.configure_server_access(TEST_API_KEY)
        self.client = TestClient(
            server_module.app,
            headers={"X-API-Key": TEST_API_KEY},
            raise_server_exceptions=False,
        )

    def tearDown(self):
        self.client.close()
        server_module._engine = None
        server_module.configure_server_access(None)
        self.engine.close()

    def test_framework_404_has_the_canonical_envelope(self):
        response = self.client.get("/api/v1/does-not-exist")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            _error("route_not_found", "Route not found."),
        )

    def test_openapi_matches_public_auth_and_error_envelopes(self):
        specification = server_module.app.openapi()
        health = specification["paths"]["/api/v1/health"]["get"]
        opening = specification["paths"]["/api/v1/turns/open"]["post"]

        self.assertEqual(specification["security"], [{"OwnerApiKey": []}])
        self.assertEqual(health["security"], [])
        self.assertNotIn("security", opening)
        self.assertEqual(
            opening["responses"]["422"]["content"]["application/json"][
                "schema"
            ]["$ref"],
            "#/components/schemas/RESTErrorEnvelope",
        )
        self.assertEqual(
            opening["responses"]["default"]["content"]["application/json"][
                "schema"
            ]["$ref"],
            "#/components/schemas/RESTErrorEnvelope",
        )

    def test_body_and_query_validation_share_one_safe_422_snapshot(self):
        expected = _error("validation_error", "Request validation failed.")

        body_error = self.client.post(
            "/api/v1/turns/open",
            json={"agent_id": "agent_erii", "user_message": "secret-input"},
        )
        query_error = self.client.get(
            "/api/v1/turns",
            params={"agent_id": "agent_erii"},
        )

        self.assertEqual(body_error.status_code, 422)
        self.assertEqual(query_error.status_code, 422)
        self.assertEqual(body_error.json(), expected)
        self.assertEqual(query_error.json(), expected)
        self.assertNotIn("secret-input", body_error.text)
        self.assertNotIn("errors", body_error.text)

    def test_missing_turn_uses_the_scoped_domain_contract(self):
        response = self.client.get(
            "/api/v1/turns/missing-turn",
            params={"agent_id": "agent_erii", "user_id": "user_one"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            _error(
                "turn_not_found",
                "Turn was not found in this relationship scope.",
            ),
        )

    def test_missing_relationship_uses_its_domain_contract(self):
        response = self.client.get(
            "/api/v1/relationship/consequences",
            params={"agent_id": "agent_erii", "user_id": "missing_user"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            _error(
                "relationship_not_found",
                "Relationship is not initialized.",
            ),
        )
        self.assertNotIn("missing_user", response.text)

    def test_turn_conflicts_do_not_echo_conflicting_content(self):
        first = self.client.post(
            "/api/v1/turns/open",
            json={
                "agent_id": "agent_erii",
                "user_id": "user_one",
                "turn_id": "stable-turn",
                "user_message": "first payload",
            },
        )
        conflict = self.client.post(
            "/api/v1/turns/open",
            json={
                "agent_id": "agent_erii",
                "user_id": "user_one",
                "turn_id": "stable-turn",
                "user_message": "second private payload",
            },
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(
            conflict.json(),
            _error(
                "turn_conflict",
                "Turn state conflicts with the requested operation.",
            ),
        )
        self.assertNotIn("second private payload", conflict.text)

    def test_capability_error_has_a_stable_non_retryable_code(self):
        self.client.post(
            "/api/v1/turns/open",
            json={
                "agent_id": "agent_erii",
                "user_id": "user_one",
                "turn_id": "continuity-turn",
                "user_message": "Hello",
            },
        )
        response = self.client.post(
            "/api/v1/turns/continuity-turn/continuity/evaluate",
            json={
                "agent_id": "agent_erii",
                "user_id": "user_one",
                "proposed_reply": "Hello",
                "persona_context_refs": [],
            },
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            _error(
                "continuity_capability_unavailable",
                "Continuity evaluation is not configured.",
            ),
        )

    def test_internal_exception_text_is_logged_but_not_returned(self):
        private_detail = "private database path and provider detail"
        with patch.object(
            self.engine,
            "recall",
            side_effect=RuntimeError(private_detail),
        ):
            response = self.client.post(
                "/api/v1/recall",
                json={
                    "agent_id": "agent_erii",
                    "user_id": "user_one",
                    "query": "hello",
                },
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json(),
            _error(
                "internal_error",
                "The server could not complete the request.",
            ),
        )
        self.assertNotIn(private_detail, response.text)


if __name__ == "__main__":
    unittest.main()
