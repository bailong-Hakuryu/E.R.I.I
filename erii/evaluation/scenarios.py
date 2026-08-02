"""Original synthetic trajectories for deterministic longitudinal evaluation."""

from __future__ import annotations

from erii.evaluation.longitudinal import (
    AuthoritySpec,
    FaultSchedule,
    GrowthSpec,
    ProjectionProbe,
    RecallProbe,
    RelationshipSpec,
    Scenario,
    TurnSpec,
)


def _authority(
    key: str,
    event_type: str,
    signal_type: str,
    summary: str,
    *,
    strength: str = "moderate",
    expected_accepted: bool = True,
    grounded: bool = True,
    reflection: str | None = None,
    growth: GrowthSpec | None = None,
) -> AuthoritySpec:
    return AuthoritySpec(
        candidate_key=key,
        event_type=event_type,
        summary=summary,
        signal_type=signal_type,
        strength=strength,
        expected_accepted=expected_accepted,
        grounded=grounded,
        persona_reflection=reflection,
        growth=growth,
    )


def single_relationship_scenario() -> Scenario:
    """128 Turns of ordinary life with sparse, source-grounded importance."""

    relationship = RelationshipSpec(
        key="aster-lin",
        agent_id="agent_aster",
        user_id="user_lin",
        persona_source=(
            "Aster is observant, candid, quietly playful, and protective of personal "
            "boundaries. Aster lets repeated ordinary experience matter without "
            "inventing intimacy."
        ),
    )
    authorities = {
        9: _authority(
            "aster-balcony-seedlings",
            "shared_experience",
            "shared_experience",
            "They replanted storm-bent balcony seedlings together.",
        ),
        31: _authority(
            "aster-reliable-return",
            "observation",
            "reliability",
            "Lin returned the borrowed field notebook on the agreed day.",
        ),
        57: _authority(
            "aster-boundary-kept",
            "observation",
            "boundary_respected",
            "Lin accepted Aster's request for an evening without messages.",
        ),
        86: _authority(
            "aster-missed-walk",
            "conflict",
            "disappointment",
            "A planned riverside walk was missed without notice.",
        ),
        119: _authority(
            "aster-walk-repair",
            "repair",
            "repair",
            "They discussed the missed walk and agreed on clearer notice.",
            reflection=(
                "Aster can acknowledge repaired disappointment without pretending "
                "the missed plan never mattered."
            ),
        ),
    }
    turns = tuple(
        TurnSpec(
            ordinal=index,
            relationship_key=relationship.key,
            turn_id=f"single-{index:03d}",
            user_message=(
                authorities[index].summary
                if index in authorities
                else (
                    f"Ordinary day {index}: the kettle clicked and the balcony light "
                    "changed before dinner."
                )
            ),
            agent_message=(
                f"Aster noted ordinary day {index} and answered without turning it "
                "into a milestone."
            ),
            authority=authorities.get(index),
        )
        for index in range(1, 129)
    )
    return Scenario(
        scenario_id="single-relationship-128/v1",
        relationships=(relationship,),
        turns=turns,
        recall_probes=(
            RecallProbe(
                probe_id="single-boundary-positive-negative/v1",
                relationship_key=relationship.key,
                query="evening without messages",
                expected_candidate_keys=("aster-boundary-kept",),
                forbidden_candidate_keys=("aster-missed-walk",),
            ),
            RecallProbe(
                probe_id="single-repair-positive-negative/v1",
                relationship_key=relationship.key,
                query="clearer notice discussed",
                expected_candidate_keys=("aster-walk-repair",),
                forbidden_candidate_keys=("aster-reliable-return",),
            ),
        ),
    )


def interleaved_relationships_scenario() -> Scenario:
    """Two similar 72-Turn histories interleaved to expose relationship leaks."""

    river = RelationshipSpec(
        key="mora-river",
        agent_id="agent_mora",
        user_id="user_river",
        persona_source=(
            "Mora is practical, curious, and warm only at a pace supported by each "
            "relationship's own history."
        ),
    )
    harbor = RelationshipSpec(
        key="mora-harbor",
        agent_id="agent_mora",
        user_id="user_harbor",
        persona_source=(
            "Mora is practical, curious, and warm only at a pace supported by each "
            "relationship's own history."
        ),
    )
    authority_by_local = {
        (river.key, 14): _authority(
            "river-copper-pin",
            "shared_experience",
            "shared_experience",
            "River and Mora found a copper pin beside an arcade map.",
        ),
        (river.key, 43): _authority(
            "river-lantern-name",
            "observation",
            "remembrance",
            "River used Lantern as the private name for the repaired map light.",
        ),
        (river.key, 69): _authority(
            "river-map-return",
            "observation",
            "reliability",
            "River returned the annotated arcade map after the festival.",
        ),
        (harbor.key, 14): _authority(
            "harbor-copper-card",
            "shared_experience",
            "shared_experience",
            "Harbor and Mora found a copper card beside an atrium map.",
        ),
        (harbor.key, 43): _authority(
            "harbor-beacon-name",
            "observation",
            "remembrance",
            "Harbor used Beacon as the private name for the repaired map light.",
        ),
        (harbor.key, 69): _authority(
            "harbor-card-return",
            "observation",
            "reliability",
            "Harbor returned the catalogued copper card after the festival.",
        ),
    }
    turns: list[TurnSpec] = []
    ordinal = 1
    for local in range(1, 73):
        for relationship, place, object_name in (
            (river, "Riverside Arcade", "copper pin"),
            (harbor, "Riverside Atrium", "copper card"),
        ):
            turns.append(
                # Exact synthetic source support is kept in the visible Turn
                # whenever an authority candidate is scheduled.
                TurnSpec(
                    ordinal=ordinal,
                    relationship_key=relationship.key,
                    turn_id=f"interleaved-{relationship.user_id}-{local:03d}",
                    user_message=(
                        authority_by_local[(relationship.key, local)].summary
                        if (relationship.key, local) in authority_by_local
                        else (
                            f"Visit {local} near {place}: the {object_name} stayed "
                            "beside the folded map."
                        )
                    ),
                    agent_message=(
                        f"Mora answered visit {local} using only this relationship's "
                        "details."
                    ),
                    authority=authority_by_local.get((relationship.key, local)),
                )
            )
            ordinal += 1
    return Scenario(
        scenario_id="interleaved-two-by-72/v1",
        relationships=(river, harbor),
        turns=tuple(turns),
        recall_probes=(
            RecallProbe(
                probe_id="interleaved-river-isolation/v1",
                relationship_key=river.key,
                query="Lantern private name",
                expected_candidate_keys=("river-lantern-name",),
                forbidden_candidate_keys=("harbor-beacon-name",),
            ),
            RecallProbe(
                probe_id="interleaved-harbor-isolation/v1",
                relationship_key=harbor.key,
                query="Beacon private name",
                expected_candidate_keys=("harbor-beacon-name",),
                forbidden_candidate_keys=("river-lantern-name",),
            ),
        ),
    )


def correction_and_growth_scenario() -> Scenario:
    """120 Turns covering correction, conflict, reflection and pending growth."""

    relationship = RelationshipSpec(
        key="sable-noor",
        agent_id="agent_sable",
        user_id="user_noor",
        persona_source=(
            "Sable is direct, imaginative, and willing to revise a belief when "
            "evidence changes. Repair may matter without erasing conflict."
        ),
    )
    authorities = {
        8: _authority(
            "sable-wrong-drink",
            "observation",
            "disclosure",
            "Noor initially described cedar tea as a favorite drink.",
        ),
        24: _authority(
            "sable-drink-correction",
            "correction",
            "neutral",
            "Noor corrected the earlier statement: citrus infusion is the favorite.",
        ),
        47: _authority(
            "sable-cancelled-rehearsal",
            "conflict",
            "disappointment",
            "A rehearsal was cancelled after both people had already travelled.",
            strength="strong",
        ),
        61: _authority(
            "sable-repair-plan",
            "repair",
            "repair",
            "They made a concrete repair plan with an earlier cancellation window.",
            reflection=(
                "Sable can treat this repair as evidence while retaining the cost of "
                "the cancelled rehearsal."
            ),
        ),
        85: _authority(
            "sable-reflection-one",
            "reflection",
            "remembrance",
            "Sable reconsidered whether advance notice can make collaboration safer.",
            reflection=(
                "Sable notices that predictable notice supports trust without making "
                "future reliability automatic."
            ),
        ),
        104: _authority(
            "sable-reflection-two",
            "reflection",
            "remembrance",
            "A second independently planned rehearsal began with timely notice.",
            reflection=(
                "Sable now has repeated evidence for valuing explicit coordination."
            ),
            growth=GrowthSpec(
                intent_key="sable-values-explicit-coordination",
                review_id="sable-growth-review-001",
                statement="Sable may value explicit coordination more strongly.",
                rationale="Two independent, source-grounded reflections support review.",
                proposed_changes={
                    "relationship_traits": {"values_explicit_coordination": True}
                },
                supporting_candidate_keys=(
                    "sable-reflection-one",
                    "sable-reflection-two",
                ),
            ),
        ),
        111: _authority(
            "sable-unsupported-authority",
            "observation",
            "remembrance",
            "An unsupported claim must not become relationship history.",
            expected_accepted=False,
            grounded=False,
        ),
    }
    turns = tuple(
        TurnSpec(
            ordinal=index,
            relationship_key=relationship.key,
            turn_id=f"correction-{index:03d}",
            user_message=(
                authorities[index].summary
                if index in authorities and authorities[index].grounded
                else (
                    f"Workshop note {index}: tools were counted, the window was checked, "
                    "and no conclusion was implied."
                )
            ),
            agent_message=(
                f"Sable acknowledged workshop note {index} without manufacturing a "
                "new memory."
            ),
            authority=authorities.get(index),
        )
        for index in range(1, 121)
    )
    return Scenario(
        scenario_id="correction-conflict-growth-120/v1",
        relationships=(relationship,),
        turns=turns,
        projection_probes=(
            ProjectionProbe(
                relationship_key=relationship.key,
                belief_key="user.favorite_infusion",
                initial_value="cedar tea",
                corrected_value="citrus infusion",
            ),
        ),
        recall_probes=(
            RecallProbe(
                probe_id="correction-current-fact/v1",
                relationship_key=relationship.key,
                query="citrus infusion favorite",
                expected_candidate_keys=("sable-drink-correction",),
                forbidden_candidate_keys=("sable-wrong-drink",),
            ),
            RecallProbe(
                probe_id="correction-growth-evidence/v1",
                relationship_key=relationship.key,
                query="timely notice planned rehearsal",
                expected_candidate_keys=("sable-reflection-two",),
                forbidden_candidate_keys=("sable-cancelled-rehearsal",),
            ),
        ),
    )


def smoke_scenario() -> Scenario:
    """A small PR trajectory that still crosses both production storage paths."""

    first = RelationshipSpec(
        key="smoke-elm",
        agent_id="agent_vesper",
        user_id="user_elm",
        persona_source="Vesper is patient, precise, and updates trust only from evidence.",
    )
    second = RelationshipSpec(
        key="smoke-ash",
        agent_id="agent_vesper",
        user_id="user_ash",
        persona_source="Vesper is patient, precise, and updates trust only from evidence.",
    )
    authority_by_ordinal = {
        3: _authority(
            "smoke-elm-reflection-one",
            "shared_experience",
            "shared_experience",
            "Elm and Vesper repaired a wind gauge together.",
            reflection="Vesper sees careful shared work as one piece of evidence.",
        ),
        4: _authority(
            "smoke-ash-marker",
            "observation",
            "remembrance",
            "Ash labelled the blue trail marker Northglass.",
        ),
        7: _authority(
            "smoke-elm-reflection-two",
            "repair",
            "repair",
            "Elm returned with the missing gauge screw and completed the repair.",
            reflection="Repeated careful repair makes explicit coordination meaningful.",
            growth=GrowthSpec(
                intent_key="smoke-explicit-coordination",
                review_id="smoke-growth-review",
                statement="Vesper may value explicit coordination in this relationship.",
                rationale="Two independent grounded events support a pending proposal.",
                proposed_changes={"values_explicit_coordination": True},
                supporting_candidate_keys=(
                    "smoke-elm-reflection-one",
                    "smoke-elm-reflection-two",
                ),
            ),
        ),
        9: _authority(
            "smoke-unsupported",
            "observation",
            "remembrance",
            "This unsupported candidate must be rejected.",
            expected_accepted=False,
            grounded=False,
        ),
    }
    turns = tuple(
        TurnSpec(
            ordinal=index,
            relationship_key=first.key if index % 2 else second.key,
            turn_id=f"smoke-{index:02d}",
            user_message=(
                authority_by_ordinal[index].summary
                if index in authority_by_ordinal and authority_by_ordinal[index].grounded
                else f"Synthetic smoke exchange {index} stays relationship scoped."
            ),
            agent_message=f"Vesper gives ordinary scoped response {index}.",
            authority=authority_by_ordinal.get(index),
        )
        for index in range(1, 13)
    )
    return Scenario(
        scenario_id="pr-smoke-12/v1",
        relationships=(first, second),
        turns=turns,
        projection_probes=(
            ProjectionProbe(
                relationship_key=first.key,
                belief_key="user.route_marker",
                initial_value="Eastglass",
                corrected_value="Northglass",
            ),
        ),
        recall_probes=(
            RecallProbe(
                probe_id="smoke-elm-isolation/v1",
                relationship_key=first.key,
                query="wind gauge repaired",
                expected_candidate_keys=("smoke-elm-reflection-one",),
                forbidden_candidate_keys=("smoke-ash-marker",),
            ),
            RecallProbe(
                probe_id="smoke-ash-isolation/v1",
                relationship_key=second.key,
                query="Northglass blue marker",
                expected_candidate_keys=("smoke-ash-marker",),
                forbidden_candidate_keys=("smoke-elm-reflection-two",),
            ),
        ),
    )


def default_fault_schedule(scenario_id: str) -> FaultSchedule:
    """Returns the checked-in deterministic failure schedule for one Scenario."""

    schedules = {
        "single-relationship-128/v1": FaultSchedule(
            retry_at=frozenset({31, 88}),
            restart_after=frozenset({41, 97}),
        ),
        "interleaved-two-by-72/v1": FaultSchedule(
            retry_at=frozenset({27, 86}),
            restart_after=frozenset({48, 110}),
        ),
        "correction-conflict-growth-120/v1": FaultSchedule(
            retry_at=frozenset({61, 111}),
            restart_after=frozenset({52, 105}),
        ),
        "pr-smoke-12/v1": FaultSchedule(
            retry_at=frozenset({5, 9}),
            restart_after=frozenset({6, 10}),
        ),
    }
    try:
        return schedules[scenario_id]
    except KeyError as exc:
        raise ValueError(f"no default fault schedule for {scenario_id!r}") from exc


__all__ = [
    "correction_and_growth_scenario",
    "default_fault_schedule",
    "interleaved_relationships_scenario",
    "single_relationship_scenario",
    "smoke_scenario",
]
