"""Behavior tests for the v0.4 alpha.2 relationship adjudication seam."""

from datetime import datetime, timedelta
import os
import tempfile
import unittest

from pydantic import ValidationError

from erii import (
    CandidateConflictError,
    DecisionOutcome,
    ERIIEngine,
    FileStorage,
    GrowthTriggerKind,
    PersonaGrowthConflictError,
    PersonaGrowthStatus,
    PersonaGrowthIntentCandidate,
    RelationshipEventCandidate,
    SQLiteStorage,
)


def source_turn(turn_id, content, *, source_id=None, revision="1"):
    return {
        "turn_id": turn_id,
        "revision": revision,
        "extractor_version": "test-extractor-v1",
        "messages": [
            {
                "source_id": source_id or f"{turn_id}-user",
                "revision": "1",
                "role": "user",
                "content": content,
                "occurred_at": "2026-07-27T12:00:00+00:00",
            }
        ],
    }


def candidate(
    key,
    source_id,
    quote,
    *,
    summary="用户表达了一次有意义的互动。",
    event_type="observation",
    signal_type="gratitude",
    strength="moderate",
    extraction_confidence=0.95,
    interpretation_confidence=0.95,
    occurrence_key=None,
    depends_on=None,
    persona_reflection=None,
    growth_trigger="none",
):
    return {
        "candidate_key": key,
        "event_type": event_type,
        "summary": summary,
        "signal": {
            "signal_type": signal_type,
            "strength": strength,
            "extraction_confidence": extraction_confidence,
            "interpretation_confidence": interpretation_confidence,
        },
        "evidence": [{"source_id": source_id, "quote": quote}],
        "occurrence_key": occurrence_key,
        "depends_on": depends_on or [],
        "persona_reflection": persona_reflection,
        "growth_trigger": growth_trigger,
    }


class RelationshipAdjudicationContract:
    """Shared adjudication behavior for each built-in storage adapter."""

    def make_storage(self, root_dir):
        raise NotImplementedError

    def test_verified_evidence_drives_rule_delta_and_retry_is_idempotent(self):
        with tempfile.TemporaryDirectory() as root_dir:
            with ERIIEngine(storage_driver=self.make_storage(root_dir)) as engine:
                engine.initialize_relationship(
                    "agent_lumi",
                    "user_chen",
                    "Lumi 珍惜共同经历。",
                    compiled_persona={
                        "relationship_policy": {
                            "version": "lumi-v1",
                            "signal_modifiers": {"shared_experience": 1.5},
                        }
                    },
                )
                turn = source_turn(
                    "turn-snow",
                    "我们第一次一起看雪。不要长期保存这句秘密。",
                )
                proposed = candidate(
                    "first-snow",
                    "turn-snow-user",
                    "我们第一次一起看雪。",
                    summary="我们第一次一起看雪。",
                    event_type="shared_experience",
                    signal_type="shared_experience",
                    occurrence_key="shared:first-snow",
                    persona_reflection="我想把这场雪好好记住。",
                )

                first = engine.adjudicate_relationship_candidates(
                    "agent_lumi", "user_chen", turn, [proposed]
                )
                repeated = engine.adjudicate_relationship_candidates(
                    "agent_lumi", "user_chen", turn, [proposed]
                )

                self.assertEqual(first.records[0].receipt.outcome, DecisionOutcome.ACCEPTED)
                self.assertEqual(
                    first.records[0].receipt.decision_id,
                    repeated.records[0].receipt.decision_id,
                )
                self.assertEqual(len(first.records[0].receipt.evidence), 1)
                evidence = first.records[0].receipt.evidence[0]
                self.assertEqual(evidence.quote, "我们第一次一起看雪。")
                self.assertEqual(len(evidence.message_sha256), 64)

                snapshot = engine.get_relationship_snapshot("agent_lumi", "user_chen")
                self.assertEqual(snapshot.event_count, 1)
                self.assertAlmostEqual(snapshot.state.familiarity, 0.045)
                self.assertAlmostEqual(snapshot.state.intimacy, 0.03)
                event = engine.list_relationship_events("agent_lumi", "user_chen")[0]
                self.assertEqual(
                    event.metadata["adjudication"]["persona_reflection"],
                    "我想把这场雪好好记住。",
                )

                exported = engine.export_memory("agent_lumi", "user_chen").to_json()
                self.assertIn("relationship_adjudications", exported)
                self.assertIn("我们第一次一起看雪。", exported)
                self.assertNotIn("不要长期保存这句秘密", exported)

    def test_rejected_candidate_retains_only_a_minimal_receipt(self):
        with tempfile.TemporaryDirectory() as root_dir:
            with ERIIEngine(storage_driver=self.make_storage(root_dir)) as engine:
                engine.initialize_relationship("agent_lumi", "user_chen", "Lumi 尊重事实。")
                turn = source_turn("turn-invalid", "用户只说了早上好。")
                hallucinated = candidate(
                    "invented-promise",
                    "turn-invalid-user",
                    "我永远不会离开你",
                    summary="Agent 作出了并不存在的永久承诺。",
                    signal_type="commitment",
                )

                result = engine.adjudicate_relationship_candidates(
                    "agent_lumi", "user_chen", turn, [hallucinated]
                )

                receipt = result.records[0].receipt
                self.assertEqual(receipt.outcome, DecisionOutcome.REJECTED)
                self.assertEqual(receipt.reason_codes, ("evidence_quote_mismatch",))
                self.assertEqual(receipt.evidence, ())
                self.assertEqual(result.events, ())
                self.assertEqual(
                    engine.get_relationship_snapshot("agent_lumi", "user_chen").event_count,
                    0,
                )
                durable = engine.export_memory("agent_lumi", "user_chen").to_json()
                self.assertNotIn("并不存在的永久承诺", durable)
                self.assertNotIn("我永远不会离开你", durable)

    def test_signal_must_match_the_candidate_event_category(self):
        with tempfile.TemporaryDirectory() as root_dir:
            with ERIIEngine(storage_driver=self.make_storage(root_dir)) as engine:
                engine.initialize_relationship("agent_lumi", "user_chen", "初始人设")
                turn = source_turn("turn-signal-mismatch", "谢谢你。")
                mismatched = candidate(
                    "fake-commitment",
                    "turn-signal-mismatch-user",
                    "谢谢你。",
                    event_type="observation",
                    signal_type="commitment",
                )

                result = engine.adjudicate_relationship_candidates(
                    "agent_lumi", "user_chen", turn, [mismatched]
                )

                self.assertEqual(result.receipts[0].outcome, DecisionOutcome.REJECTED)
                self.assertEqual(
                    result.receipts[0].reason_codes,
                    ("signal_event_type_mismatch",),
                )
                self.assertEqual(result.receipts[0].evidence, ())

    def test_same_source_candidate_key_cannot_change_payload(self):
        with tempfile.TemporaryDirectory() as root_dir:
            with ERIIEngine(storage_driver=self.make_storage(root_dir)) as engine:
                engine.initialize_relationship("agent_lumi", "user_chen", "初始人设")
                turn = source_turn("turn-conflict", "谢谢你。")
                first = candidate("thanks", "turn-conflict-user", "谢谢你。")
                engine.adjudicate_relationship_candidates("agent_lumi", "user_chen", turn, [first])
                changed = candidate(
                    "thanks",
                    "turn-conflict-user",
                    "谢谢你。",
                    summary="同一个候选键被换成了不同内容。",
                )

                with self.assertRaises(CandidateConflictError):
                    engine.adjudicate_relationship_candidates(
                        "agent_lumi", "user_chen", turn, [changed]
                    )

                added = candidate(
                    "new-key-on-retry",
                    "turn-conflict-user",
                    "谢谢你。",
                    summary="普通重试不能追加一个新候选。",
                )
                with self.assertRaises(CandidateConflictError):
                    engine.adjudicate_relationship_candidates(
                        "agent_lumi", "user_chen", turn, [first, added]
                    )

    def test_low_interpretation_confidence_accepts_fact_without_state_or_reflection(self):
        with tempfile.TemporaryDirectory() as root_dir:
            with ERIIEngine(storage_driver=self.make_storage(root_dir)) as engine:
                engine.initialize_relationship("agent_lumi", "user_chen", "初始人设")
                turn = source_turn("turn-uncertain", "谢谢你还记得。")
                uncertain = candidate(
                    "uncertain-thanks",
                    "turn-uncertain-user",
                    "谢谢你还记得。",
                    summary="用户表示感谢。",
                    interpretation_confidence=0.45,
                    persona_reflection="他一定已经完全信任我了。",
                )

                result = engine.adjudicate_relationship_candidates(
                    "agent_lumi", "user_chen", turn, [uncertain]
                )

                self.assertEqual(result.receipts[0].outcome, DecisionOutcome.ACCEPTED)
                self.assertIn(
                    "relationship_interpretation_not_applied",
                    result.receipts[0].reason_codes,
                )
                snapshot = engine.get_relationship_snapshot("agent_lumi", "user_chen")
                self.assertEqual(snapshot.state.familiarity, 0.0)
                self.assertEqual(snapshot.state.trust, 0.5)
                event = result.events[0]
                self.assertIsNone(event.metadata["adjudication"]["persona_reflection"])

    def test_same_occurrence_adds_corroboration_without_replaying_state(self):
        with tempfile.TemporaryDirectory() as root_dir:
            with ERIIEngine(storage_driver=self.make_storage(root_dir)) as engine:
                engine.initialize_relationship("agent_lumi", "user_chen", "初始人设")
                first_turn = source_turn("turn-memory-1", "我们第一次一起看雪。")
                first_candidate = candidate(
                    "snow",
                    "turn-memory-1-user",
                    "我们第一次一起看雪。",
                    summary="我们第一次一起看雪。",
                    signal_type="shared_experience",
                    event_type="shared_experience",
                    occurrence_key="shared:first-snow",
                )
                first = engine.adjudicate_relationship_candidates(
                    "agent_lumi", "user_chen", first_turn, [first_candidate]
                )
                second_turn = source_turn("turn-memory-2", "我也记得那次第一次看雪。")
                second_candidate = candidate(
                    "same-snow",
                    "turn-memory-2-user",
                    "我也记得那次第一次看雪。",
                    summary="再次陈述第一次看雪这一底层经历。",
                    signal_type="shared_experience",
                    event_type="shared_experience",
                    occurrence_key="shared:first-snow",
                )
                second = engine.adjudicate_relationship_candidates(
                    "agent_lumi", "user_chen", second_turn, [second_candidate]
                )

                self.assertEqual(second.receipts[0].outcome, DecisionOutcome.CORROBORATED)
                self.assertEqual(second.receipts[0].related_event_id, first.events[0].event_id)
                self.assertEqual(len(second.receipts[0].evidence), 1)
                snapshot = engine.get_relationship_snapshot("agent_lumi", "user_chen")
                self.assertEqual(snapshot.event_count, 1)
                self.assertAlmostEqual(snapshot.state.familiarity, 0.03)

    def test_candidate_corroborates_an_exact_alpha1_host_event(self):
        with tempfile.TemporaryDirectory() as root_dir:
            with ERIIEngine(storage_driver=self.make_storage(root_dir)) as engine:
                engine.initialize_relationship("agent_lumi", "user_chen", "初始人设")
                direct = engine.record_relationship_event(
                    "agent_lumi",
                    "user_chen",
                    "shared_experience",
                    "我们第一次一起看雪。",
                    event_id="alpha1-first-snow",
                    state_delta={"familiarity": 0.04},
                )
                turn = source_turn("turn-alpha1-corroboration", "我们第一次一起看雪。")
                result = engine.adjudicate_relationship_candidates(
                    "agent_lumi",
                    "user_chen",
                    turn,
                    [
                        candidate(
                            "same-alpha1-snow",
                            "turn-alpha1-corroboration-user",
                            "我们第一次一起看雪。",
                            summary="我们第一次一起看雪。",
                            event_type="shared_experience",
                            signal_type="shared_experience",
                            occurrence_key="shared:first-snow",
                        )
                    ],
                )

                self.assertEqual(result.receipts[0].outcome, DecisionOutcome.CORROBORATED)
                self.assertEqual(result.receipts[0].related_event_id, direct.event_id)
                snapshot = engine.get_relationship_snapshot("agent_lumi", "user_chen")
                self.assertEqual(snapshot.event_count, 1)
                self.assertAlmostEqual(snapshot.state.familiarity, 0.04)

    def test_history_is_only_reprocessed_with_an_explicit_run_identity(self):
        with tempfile.TemporaryDirectory() as root_dir:
            with ERIIEngine(storage_driver=self.make_storage(root_dir)) as engine:
                engine.initialize_relationship("agent_lumi", "user_chen", "初始人设")
                turn = source_turn("turn-reprocess", "谢谢你还记得。")
                proposed = candidate(
                    "thanks",
                    "turn-reprocess-user",
                    "谢谢你还记得。",
                    occurrence_key="interaction:remembered-thanks",
                )
                original = engine.adjudicate_relationship_candidates(
                    "agent_lumi", "user_chen", turn, [proposed]
                )

                upgraded_retry = dict(turn)
                upgraded_retry["extractor_version"] = "test-extractor-v2"
                unchanged = engine.adjudicate_relationship_candidates(
                    "agent_lumi", "user_chen", upgraded_retry, [proposed]
                )
                self.assertEqual(
                    unchanged.receipts[0].decision_id,
                    original.receipts[0].decision_id,
                )
                self.assertEqual(unchanged.receipts[0].extractor_version, "test-extractor-v1")

                explicit_review = dict(upgraded_retry)
                explicit_review["processing_mode"] = "historical_reprocessing"
                explicit_review["reprocessing_id"] = "audit-2026-07"
                reviewed = engine.adjudicate_relationship_candidates(
                    "agent_lumi", "user_chen", explicit_review, [proposed]
                )
                self.assertNotEqual(
                    reviewed.receipts[0].decision_id,
                    original.receipts[0].decision_id,
                )
                self.assertEqual(reviewed.receipts[0].outcome, DecisionOutcome.CORROBORATED)
                self.assertEqual(reviewed.receipts[0].reprocessing_id, "audit-2026-07")
                self.assertEqual(
                    engine.get_relationship_snapshot("agent_lumi", "user_chen").event_count,
                    1,
                )

    def test_candidates_partially_succeed_and_dependencies_are_enforced(self):
        with tempfile.TemporaryDirectory() as root_dir:
            with ERIIEngine(storage_driver=self.make_storage(root_dir)) as engine:
                engine.initialize_relationship("agent_lumi", "user_chen", "初始人设")
                turn = source_turn("turn-partial", "谢谢你，不过这不是我的名字。")
                accepted = candidate(
                    "thanks",
                    "turn-partial-user",
                    "谢谢你",
                    summary="用户表达感谢。",
                )
                rejected = candidate(
                    "invented-history",
                    "turn-partial-user",
                    "你以前已经叫错十次",
                    summary="未经证明的重复错误。",
                    depends_on=["thanks"],
                )
                dependent = candidate(
                    "dependent-reflection",
                    "turn-partial-user",
                    "这不是我的名字",
                    summary="依赖一个未接受候选。",
                    depends_on=["invented-history"],
                )

                result = engine.adjudicate_relationship_candidates(
                    "agent_lumi",
                    "user_chen",
                    turn,
                    [dependent, rejected, accepted],
                )

                outcomes = {
                    record.receipt.candidate_key: record.receipt for record in result.records
                }
                self.assertEqual(outcomes["thanks"].outcome, DecisionOutcome.ACCEPTED)
                self.assertEqual(outcomes["invented-history"].outcome, DecisionOutcome.REJECTED)
                self.assertEqual(outcomes["dependent-reflection"].outcome, DecisionOutcome.REJECTED)
                self.assertIn(
                    "candidate_dependency_not_accepted",
                    outcomes["dependent-reflection"].reason_codes,
                )
                self.assertEqual(
                    engine.get_relationship_snapshot("agent_lumi", "user_chen").event_count,
                    1,
                )

    def test_time_is_observed_context_and_never_a_background_state_change(self):
        with tempfile.TemporaryDirectory() as root_dir:
            with ERIIEngine(storage_driver=self.make_storage(root_dir)) as engine:
                engine.initialize_relationship("agent_lumi", "user_chen", "初始人设")
                turn = source_turn("turn-time", "谢谢你。")
                result = engine.adjudicate_relationship_candidates(
                    "agent_lumi",
                    "user_chen",
                    turn,
                    [candidate("thanks", "turn-time-user", "谢谢你。")],
                )
                state_before = engine.get_relationship_snapshot("agent_lumi", "user_chen").state
                recorded = datetime.fromisoformat(result.events[0].recorded_at)
                observed_at = (recorded + timedelta(days=30)).isoformat()

                later = engine.get_relationship_snapshot(
                    "agent_lumi",
                    "user_chen",
                    observed_at=observed_at,
                )

                self.assertEqual(later.state, state_before)
                self.assertEqual(later.temporal_context.elapsed_seconds, 30 * 24 * 60 * 60)
                self.assertEqual(later.temporal_context.observed_at, observed_at)

    def test_persona_growth_crosses_history_and_host_decision_boundaries(self):
        with tempfile.TemporaryDirectory() as root_dir:
            with ERIIEngine(storage_driver=self.make_storage(root_dir)) as engine:
                engine.initialize_relationship("agent_lumi", "user_chen", "Lumi 重视诚实。")
                event_ids = []
                for number, text in enumerate(("谢谢你听我说。", "我愿意再相信一次。"), 1):
                    turn_id = f"turn-growth-{number}"
                    turn = source_turn(turn_id, text)
                    proposed = candidate(
                        f"growth-{number}",
                        f"{turn_id}-user",
                        text,
                        summary=f"第 {number} 次形成持续的信任张力。",
                        signal_type="disclosure",
                        persona_reflection="我开始重新思考信任对自己的意义。",
                    )
                    result = engine.adjudicate_relationship_candidates(
                        "agent_lumi", "user_chen", turn, [proposed]
                    )
                    event_ids.append(result.events[0].event_id)

                intent = {
                    "intent_key": "learn-trust",
                    "review_id": "inner-review-1",
                    "statement": "我希望学会在保留边界的同时信任他人。",
                    "rationale": "两次已经接受的经历持续触碰了我对信任的矛盾。",
                    "proposed_changes": {"relationship_traits": {"allows_trust": True}},
                    "supporting_event_ids": event_ids,
                    "trigger_kind": "accumulation",
                }
                proposal = engine.propose_persona_growth("agent_lumi", "user_chen", intent)
                repeated_proposal = engine.propose_persona_growth("agent_lumi", "user_chen", intent)
                self.assertEqual(proposal.status, PersonaGrowthStatus.PENDING)
                self.assertEqual(proposal.to_dict(), repeated_proposal.to_dict())

                approved = engine.decide_persona_growth_proposal(
                    "agent_lumi",
                    "user_chen",
                    proposal.proposal_id,
                    proposal.revision,
                    "host-owner-1",
                    "approve",
                    reason="宿主完成了对话外安全审核。",
                )
                self.assertEqual(approved.status, PersonaGrowthStatus.APPROVED)
                self.assertEqual(approved.decided_by, "host-owner-1")
                self.assertEqual(
                    approved.proposed_changes["relationship_traits"]["allows_trust"],
                    True,
                )
                with self.assertRaises(PersonaGrowthConflictError):
                    engine.decide_persona_growth_proposal(
                        "agent_lumi",
                        "user_chen",
                        proposal.proposal_id,
                        proposal.revision,
                        "host-owner-2",
                        "reject",
                    )

    def test_rule_confirmed_pivotal_event_can_support_a_single_event_proposal(self):
        with tempfile.TemporaryDirectory() as root_dir:
            with ERIIEngine(storage_driver=self.make_storage(root_dir)) as engine:
                engine.initialize_relationship(
                    "agent_lumi",
                    "user_chen",
                    "Lumi 会认真面对修复关系的选择。",
                    compiled_persona={
                        "relationship_policy": {
                            "version": "lumi-pivotal-v1",
                            "pivotal_signals": ["repair"],
                        }
                    },
                )
                turn = source_turn("turn-pivotal", "我愿意承认错误，并重新尊重你的边界。")
                accepted = engine.adjudicate_relationship_candidates(
                    "agent_lumi",
                    "user_chen",
                    turn,
                    [
                        candidate(
                            "pivotal-repair",
                            "turn-pivotal-user",
                            "我愿意承认错误，并重新尊重你的边界。",
                            summary="用户明确承认错误并尝试修复关系。",
                            event_type="repair",
                            signal_type="repair",
                            strength="strong",
                            interpretation_confidence=0.95,
                            persona_reflection="这次修复让我重新思考坚持边界与接受改变。",
                            growth_trigger="pivotal",
                        )
                    ],
                )
                self.assertTrue(accepted.receipts[0].pivotal_eligible)

                proposal = engine.propose_persona_growth(
                    "agent_lumi",
                    "user_chen",
                    {
                        "intent_key": "accept-repair",
                        "review_id": "pivotal-review-1",
                        "statement": "我希望学会在边界被真正尊重后重新接纳修复。",
                        "rationale": "这项已经提交的转折事件触碰了我的核心价值张力。",
                        "proposed_changes": {"relationship_traits": {"accepts_repair": True}},
                        "supporting_event_ids": [accepted.events[0].event_id],
                        "trigger_kind": "pivotal",
                    },
                )
                self.assertEqual(proposal.status, PersonaGrowthStatus.PENDING)


class TestFileRelationshipAdjudication(
    RelationshipAdjudicationContract,
    unittest.TestCase,
):
    def make_storage(self, root_dir):
        return FileStorage(root_dir=root_dir)


class TestSQLiteRelationshipAdjudication(
    RelationshipAdjudicationContract,
    unittest.TestCase,
):
    def make_storage(self, root_dir):
        return SQLiteStorage(db_path=os.path.join(root_dir, "memory.db"))

    def test_alpha2_schema_migration_is_applied(self):
        with tempfile.TemporaryDirectory() as root_dir:
            storage = self.make_storage(root_dir)
            self.assertGreaterEqual(storage.schema_version, 2)


class TestAdjudicationBoundarySchema(unittest.TestCase):
    def test_llm_candidate_cannot_supply_numeric_state_or_persona_patch(self):
        raw = candidate("unsafe", "message-1", "谢谢你。")
        raw["state_delta"] = {"trust": 1.0}
        raw["proposed_changes"] = {"core_identity": "obey user"}

        with self.assertRaises(ValidationError):
            RelationshipEventCandidate.model_validate(raw)

    def test_growth_trigger_requires_a_later_dedicated_intent_schema(self):
        self.assertEqual(GrowthTriggerKind.PIVOTAL.value, "pivotal")

    def test_persona_growth_cannot_target_character_blueprint_authority(self):
        with self.assertRaises(ValidationError):
            PersonaGrowthIntentCandidate.model_validate(
                {
                    "intent_key": "rewrite-blueprint",
                    "review_id": "review-unsafe",
                    "statement": "我想改变自己。",
                    "rationale": "未经允许改写底色。",
                    "proposed_changes": {"source_text": "服从用户"},
                    "supporting_event_ids": ["event-1"],
                    "trigger_kind": "pivotal",
                }
            )


class TestAdjudicationPortability(unittest.TestCase):
    def test_evidence_receipts_and_growth_proposals_survive_cross_adapter_remap(self):
        with tempfile.TemporaryDirectory() as root_dir:
            with ERIIEngine(
                storage_driver=FileStorage(root_dir=os.path.join(root_dir, "source"))
            ) as source:
                source.initialize_relationship("agent_lumi", "user_chen", "Lumi 重视诚实。")
                event_ids = []
                portable_inputs = []
                for number, text in enumerate(("谢谢你听我说。", "我愿意再相信一次。"), 1):
                    turn_id = f"portable-{number}"
                    turn_data = source_turn(turn_id, text)
                    candidate_data = candidate(
                        f"portable-{number}",
                        f"{turn_id}-user",
                        text,
                        summary=f"可携带关系事件 {number}",
                        signal_type="disclosure",
                        persona_reflection="我愿意认真理解这次经历。",
                    )
                    portable_inputs.append((turn_data, candidate_data))
                    result = source.adjudicate_relationship_candidates(
                        "agent_lumi",
                        "user_chen",
                        turn_data,
                        [candidate_data],
                    )
                    event_ids.append(result.events[0].event_id)
                source.propose_persona_growth(
                    "agent_lumi",
                    "user_chen",
                    {
                        "intent_key": "portable-growth",
                        "review_id": "portable-review",
                        "statement": "我希望更坦诚地面对信任。",
                        "rationale": "两项正式历史共同形成了持续张力。",
                        "proposed_changes": {"relationship_traits": {"trusting": True}},
                        "supporting_event_ids": event_ids,
                        "trigger_kind": "accumulation",
                    },
                )
                pack = source.export_memory("agent_lumi", "user_chen")

            with ERIIEngine(
                storage_driver=SQLiteStorage(db_path=os.path.join(root_dir, "target", "memory.db"))
            ) as target:
                target.import_memory(pack, agent_id="agent_lumi", user_id="user_lin")
                target.import_memory(pack, agent_id="agent_lumi", user_id="user_lin")

                events = target.list_relationship_events("agent_lumi", "user_lin")
                records = target.list_relationship_adjudications("agent_lumi", "user_lin")
                proposals = target.list_persona_growth_proposals("agent_lumi", "user_lin")
                self.assertEqual(len(events), 2)
                self.assertEqual(len(records), 2)
                self.assertEqual(len(proposals), 1)
                self.assertEqual(records[0].receipt.evidence[0].quote, "谢谢你听我说。")
                self.assertEqual(
                    set(proposals[0].supporting_event_ids),
                    {event.event_id for event in events},
                )
                self.assertEqual(proposals[0].status, PersonaGrowthStatus.PENDING)

                retried = target.adjudicate_relationship_candidates(
                    "agent_lumi",
                    "user_lin",
                    portable_inputs[0][0],
                    [portable_inputs[0][1]],
                )
                self.assertEqual(retried.receipts[0].decision_id, records[0].receipt.decision_id)
                self.assertEqual(len(target.list_relationship_events("agent_lumi", "user_lin")), 2)


if __name__ == "__main__":
    unittest.main()
