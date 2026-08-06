from dataclasses import FrozenInstanceError, replace

import pytest

from erii.core.consequence import NarrativeTensionProjector
from erii.models.consequence import (
    ConsequenceConflictError,
    NarrativeTensionConflictError,
    NarrativeTensionLink,
    NarrativeTensionOutcome,
    NarrativeTensionProjection,
    NarrativeTensionUpdate,
    RelationshipConsequence,
    RelationshipConsequenceKind,
)


def _consequence(
    consequence_id: str = "consequence-1",
    *,
    relationship_id: str = "relationship-1",
    tension_id: str = "tension-1",
    effects=(
        RelationshipConsequenceKind.HARM,
        RelationshipConsequenceKind.BOUNDARY_EXPRESSION,
    ),
    summary: str = "The refusal caused harm while expressing a boundary.",
    recorded_at: str = "2026-08-06T10:00:00+00:00",
) -> RelationshipConsequence:
    return RelationshipConsequence(
        consequence_id=consequence_id,
        relationship_id=relationship_id,
        tension_id=tension_id,
        source_turn_id=f"turn-{relationship_id}-{consequence_id}",
        source_revision="1",
        source_decision_id=f"decision-{relationship_id}-{consequence_id}",
        source_event_id=f"event-{relationship_id}-{consequence_id}",
        source_message_id=f"message-{relationship_id}-{consequence_id}",
        effects=effects,
        summary=summary,
        recorded_at=recorded_at,
    )


def _link(
    link_id: str = "link-1",
    *,
    relationship_id: str = "relationship-1",
    tension_id: str = "tension-1",
    consequence_id: str = "consequence-1",
    outcome: NarrativeTensionOutcome = NarrativeTensionOutcome.ADDRESSED_UNRESOLVED,
    summary: str = "The parties addressed the harm but did not resolve it.",
    recorded_at: str = "2026-08-06T11:00:00+00:00",
) -> NarrativeTensionLink:
    return NarrativeTensionLink(
        link_id=link_id,
        relationship_id=relationship_id,
        tension_id=tension_id,
        consequence_id=consequence_id,
        source_turn_id=f"turn-{relationship_id}-{link_id}",
        source_revision="1",
        source_decision_id=f"decision-{relationship_id}-{link_id}",
        source_event_id=f"event-{relationship_id}-{link_id}",
        outcome=outcome,
        summary=summary,
        recorded_at=recorded_at,
    )


def test_consequence_kind_and_tension_outcome_contracts_are_complete() -> None:
    assert {item.value for item in RelationshipConsequenceKind} == {
        "harm",
        "comfort",
        "refusal",
        "anger",
        "boundary_expression",
        "trust_decrease",
        "temporary_distance",
        "relationship_end",
        "repair_attempt",
        "repair_refused",
        "conflict",
    }
    assert {item.value for item in NarrativeTensionOutcome} == {
        "unaddressed",
        "addressed_unresolved",
        "mutually_reconciled",
        "boundary_stabilized",
        "relationship_ended",
        "superseded",
    }


def test_durable_records_are_immutable_strict_and_round_trip() -> None:
    consequence = _consequence(
        effects=(
            RelationshipConsequenceKind.BOUNDARY_EXPRESSION,
            RelationshipConsequenceKind.HARM,
        )
    )
    assert consequence.effects == (
        RelationshipConsequenceKind.HARM,
        RelationshipConsequenceKind.BOUNDARY_EXPRESSION,
    )
    assert consequence.kind == RelationshipConsequenceKind.HARM
    assert RelationshipConsequence.from_dict(consequence.to_dict()) == consequence

    link = _link()
    assert NarrativeTensionLink.from_dict(link.to_dict()) == link
    assert NarrativeTensionUpdate is NarrativeTensionLink

    with pytest.raises(FrozenInstanceError):
        consequence.summary = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        link.outcome = NarrativeTensionOutcome.SUPERSEDED  # type: ignore[misc]

    assert consequence.same_payload_as(
        replace(consequence, recorded_at="2030-01-01T00:00:00+00:00")
    )
    assert not consequence.same_payload_as(replace(consequence, summary="Different"))
    assert link.same_payload_as(
        replace(link, recorded_at="2030-01-01T00:00:00+00:00")
    )
    assert not link.same_payload_as(replace(link, summary="Different"))


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda value: {**value, "unknown": "field"}, "unknown or missing"),
        (
            lambda value: {key: item for key, item in value.items() if key != "summary"},
            "unknown or missing",
        ),
        (lambda value: {**value, "effects": "harm"}, "must be an array"),
        (lambda value: {**value, "source_revision": 1}, "must be a string"),
    ],
)
def test_consequence_from_dict_rejects_noncanonical_wire_data(mutator, message) -> None:
    raw = _consequence().to_dict()
    with pytest.raises(ValueError, match=message):
        RelationshipConsequence.from_dict(mutator(raw))


def test_model_validation_rejects_empty_duplicate_and_invalid_values() -> None:
    with pytest.raises(ValueError, match="non-empty sequence"):
        _consequence(effects=())
    with pytest.raises(ValueError, match="duplicates"):
        _consequence(
            effects=(
                RelationshipConsequenceKind.HARM,
                RelationshipConsequenceKind.HARM,
            )
        )
    with pytest.raises(ValueError, match="supported value"):
        _consequence(effects=("invented",))
    with pytest.raises(ValueError, match="non-empty string"):
        _consequence(summary="  ")
    with pytest.raises(ValueError, match="cannot use unaddressed"):
        _link(outcome=NarrativeTensionOutcome.UNADDRESSED)

    raw_link = _link().to_dict()
    with pytest.raises(ValueError, match="unknown or missing"):
        NarrativeTensionLink.from_dict({**raw_link, "extra": "field"})
    with pytest.raises(ValueError, match="must be a string"):
        NarrativeTensionLink.from_dict({**raw_link, "outcome": 1})


@pytest.mark.parametrize("outcome", tuple(NarrativeTensionOutcome))
def test_projection_exposes_all_six_tension_states(
    outcome: NarrativeTensionOutcome,
) -> None:
    consequence = _consequence()
    links = () if outcome == NarrativeTensionOutcome.UNADDRESSED else (
        _link(outcome=outcome),
    )

    projection = NarrativeTensionProjector.project((consequence,), links)[0]

    assert projection.outcome == outcome
    assert projection.effects == consequence.effects
    assert projection.source_turn_id == consequence.source_turn_id
    assert projection.source_message_id == consequence.source_message_id
    assert projection.link_ids == (() if not links else ("link-1",))
    assert NarrativeTensionProjection.from_dict(projection.to_dict()) == projection


def test_projection_orders_sources_deterministically_and_retains_every_link() -> None:
    consequence_2 = _consequence(
        "consequence-2",
        tension_id="tension-2",
        recorded_at="2026-08-06T09:00:00+00:00",
    )
    consequence_1 = _consequence(recorded_at="2026-08-06T10:00:00+00:00")
    terminal = _link(
        "link-z",
        outcome=NarrativeTensionOutcome.BOUNDARY_STABILIZED,
        summary="The boundary is now stable.",
        recorded_at="2026-08-06T12:00:00+00:00",
    )
    addressed = _link(
        "link-a",
        outcome=NarrativeTensionOutcome.ADDRESSED_UNRESOLVED,
        summary="The consequence was addressed.",
        recorded_at="2026-08-06T12:00:00+00:00",
    )

    first = NarrativeTensionProjector.project(
        (consequence_1, consequence_2),
        (terminal, addressed),
    )
    second = NarrativeTensionProjector.project(
        (consequence_2, consequence_1),
        (addressed, terminal),
    )

    assert first == second
    assert tuple(item.consequence_id for item in first) == (
        "consequence-2",
        "consequence-1",
    )
    assert first[1].outcome == NarrativeTensionOutcome.BOUNDARY_STABILIZED
    assert first[1].summary == "The boundary is now stable."
    assert first[1].link_ids == ("link-a", "link-z")


def test_elapsed_time_never_resolves_a_tension() -> None:
    old_consequence = _consequence(recorded_at="2000-01-01T00:00:00+00:00")

    projection = NarrativeTensionProjector.project((old_consequence,), ())[0]

    assert projection.outcome == NarrativeTensionOutcome.UNADDRESSED
    assert projection.summary == old_consequence.summary
    assert projection.link_ids == ()


def test_terminal_tensions_cannot_be_silently_reopened_but_can_be_superseded() -> None:
    consequence = _consequence()
    terminal = _link(
        "link-1",
        outcome=NarrativeTensionOutcome.MUTUALLY_RECONCILED,
        recorded_at="2026-08-06T11:00:00+00:00",
    )
    reopening = _link(
        "link-2",
        outcome=NarrativeTensionOutcome.ADDRESSED_UNRESOLVED,
        recorded_at="2026-08-06T12:00:00+00:00",
    )
    superseding = replace(
        reopening,
        outcome=NarrativeTensionOutcome.SUPERSEDED,
        summary="A new explicit tension supersedes this one.",
    )

    with pytest.raises(NarrativeTensionConflictError, match="cannot be silently reopened"):
        NarrativeTensionProjector.project((consequence,), (terminal, reopening))

    projection = NarrativeTensionProjector.project(
        (consequence,),
        (superseding, terminal),
    )[0]
    assert projection.outcome == NarrativeTensionOutcome.SUPERSEDED
    assert projection.link_ids == ("link-1", "link-2")


def test_projector_rejects_dangling_cross_relationship_and_tension_mismatches() -> None:
    consequence = _consequence()

    with pytest.raises(NarrativeTensionConflictError, match="missing consequence"):
        NarrativeTensionProjector.project((), (_link(),))

    with pytest.raises(NarrativeTensionConflictError, match="another relationship"):
        NarrativeTensionProjector.project(
            (consequence,),
            (_link(relationship_id="relationship-2"),),
        )

    with pytest.raises(NarrativeTensionConflictError, match="does not match"):
        NarrativeTensionProjector.project(
            (consequence,),
            (_link(tension_id="another-tension"),),
        )


def test_projector_rejects_duplicate_consequence_tension_and_link_conflicts() -> None:
    consequence = _consequence()
    with pytest.raises(ConsequenceConflictError, match="conflicting journal payloads"):
        NarrativeTensionProjector.project(
            (consequence, replace(consequence, summary="Conflicting")),
            (),
        )

    with pytest.raises(NarrativeTensionConflictError, match="already rooted"):
        NarrativeTensionProjector.project(
            (consequence, _consequence("consequence-2")),
            (),
        )

    link = _link()
    with pytest.raises(NarrativeTensionConflictError, match="conflicting journal payloads"):
        NarrativeTensionProjector.project(
            (consequence,),
            (link, replace(link, summary="Conflicting")),
        )


def test_exact_replays_are_idempotent_even_when_recorded_at_differs() -> None:
    consequence = _consequence()
    consequence_replay = replace(
        consequence,
        recorded_at="2026-08-06T13:00:00+00:00",
    )
    link = _link()
    link_replay = replace(link, recorded_at="2026-08-06T14:00:00+00:00")

    projection = NarrativeTensionProjector.project(
        (consequence_replay, consequence),
        (link_replay, link),
    )[0]

    assert projection.link_ids == ("link-1",)
    assert projection.outcome == NarrativeTensionOutcome.ADDRESSED_UNRESOLVED


def test_relationships_with_the_same_local_ids_project_independently() -> None:
    first = _consequence(relationship_id="relationship-1")
    second = _consequence(relationship_id="relationship-2")
    first_link = _link(
        relationship_id="relationship-1",
        outcome=NarrativeTensionOutcome.ADDRESSED_UNRESOLVED,
    )
    second_link = _link(
        relationship_id="relationship-2",
        outcome=NarrativeTensionOutcome.RELATIONSHIP_ENDED,
    )

    projections = NarrativeTensionProjector.project(
        (second, first),
        (second_link, first_link),
    )
    by_relationship = {item.relationship_id: item for item in projections}

    assert by_relationship["relationship-1"].outcome == (
        NarrativeTensionOutcome.ADDRESSED_UNRESOLVED
    )
    assert by_relationship["relationship-2"].outcome == (
        NarrativeTensionOutcome.RELATIONSHIP_ENDED
    )
    assert by_relationship["relationship-1"].link_ids == ("link-1",)
    assert by_relationship["relationship-2"].link_ids == ("link-1",)
