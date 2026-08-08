"""Regression tests for fail-closed, non-logging provider parsing."""

import pytest
from erii.models.continuity import ContinuityEvaluationRequest
from erii.models.continuity_evidence import (
    ContinuityEvidenceKind,
    ContinuityEvidenceRef,
)

from erii_deepseek_continuity import ParsingError
from erii_deepseek_continuity.response_parser import parse_to_decision


def _request() -> ContinuityEvaluationRequest:
    persona_ref = ContinuityEvidenceRef.create(
        ContinuityEvidenceKind.PERSONA_CLAIM,
        {
            "manifest_id": "manifest",
            "content_fingerprint": "1" * 64,
            "claim_id": "claim",
        },
    )
    return ContinuityEvaluationRequest(
        turn_id="turn",
        relationship_id="relationship",
        persona_id="persona",
        user_message="user message",
        proposed_reply="reply",
        persona_manifest_id="manifest",
        context_baseline_fingerprint="0" * 64,
        persona_context_refs=(persona_ref,),
        relationship_context_refs=(),
        voice_pattern_activations=(),
    )


def test_invalid_json_never_reaches_stderr_or_exception_chain(capsys) -> None:
    secret = "provider-private-raw-response"
    with pytest.raises(ParsingError) as raised:
        parse_to_decision(
            response={
                "content": "{" + secret,
                "finish_reason": "stop",
            },
            request=_request(),
            resolved_evidence=(),
            resolved_activations=(),
        )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert secret not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_incomplete_response_fails_before_parsing(capsys) -> None:
    with pytest.raises(ParsingError, match="incomplete_response"):
        parse_to_decision(
            response={"content": "sensitive partial output", "finish_reason": "length"},
            request=_request(),
            resolved_evidence=(),
            resolved_activations=(),
        )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
