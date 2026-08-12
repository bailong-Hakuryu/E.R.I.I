"""Regression tests for previously reproduced C0 defects."""

import pytest
from pydantic import ValidationError

from erii.deliberation.fake_actor import create_minimal_decision
from erii.deliberation.schemas import MessagePart, VisibleReplyEnvelopeV1


def test_nested_collections_are_immutable() -> None:
    decision = create_minimal_decision()
    assert isinstance(decision.frame.situation_appraisals, tuple)
    with pytest.raises(AttributeError):
        decision.frame.situation_appraisals.append("injected")


def test_model_copy_revalidates_updates() -> None:
    part = MessagePart(part_id="valid", kind="text", exact_utf8="content")
    with pytest.raises(ValidationError):
        part.model_copy(update={"kind": "INVALID_ENUM"})
    with pytest.raises(ValueError, match="unknown fields"):
        part.model_copy(update={"unknown": "value"})


def test_duplicate_reply_part_ids_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate part_id"):
        VisibleReplyEnvelopeV1(
            parts=(
                MessagePart(part_id="duplicate", kind="text", exact_utf8="one"),
                MessagePart(part_id="duplicate", kind="text", exact_utf8="two"),
            )
        )


def test_sensitive_text_is_absent_from_repr() -> None:
    canary = "SECRET_CONTENT_DO_NOT_LOG"
    part = MessagePart(part_id="secret", kind="text", exact_utf8=canary)
    assert canary not in repr(part)


def test_validated_model_copy_returns_a_distinct_value() -> None:
    part = MessagePart(part_id="part", kind="text", exact_utf8="before")
    replacement = part.model_copy(update={"exact_utf8": "after"})
    assert replacement is not part
    assert replacement.exact_utf8 == "after"
    assert part.exact_utf8 == "before"


@pytest.mark.parametrize("invisible", ["\u200b", "\ufeff", "\u2060", "\n", "\t"])
def test_schema_identifiers_share_the_strict_identifier_policy(invisible: str) -> None:
    with pytest.raises(ValidationError):
        MessagePart(part_id=f"part{invisible}id", kind="text", exact_utf8="allowed")
