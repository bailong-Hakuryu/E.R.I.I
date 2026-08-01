"""Engine-level pre-delivery continuity contracts."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import os
import tempfile
from threading import Event, Lock
import unittest

from erii import (
    ERIIEngine,
    FileStorage,
    PersonaManifestRequiredError,
    SQLiteStorage,
)
from erii.models.continuity import (
    ContinuityAxis,
    ContinuityEvaluatorDescriptor,
    InteractionContextEvaluatorDescriptor,
)
from erii.models.continuity_evidence import (
    ContinuityEvidenceKind,
    ContinuityEvidenceRef,
)
from erii.models.relationship import RelationshipEvent
from erii.models.turn import ContinuityVerdict, TurnStatus
from erii.models.voice_trace import VoiceActivationTrace


class _ContinuityEvaluator:
    descriptor = ContinuityEvaluatorDescriptor(
        evaluator_id="tests.continuity-evaluator",
        evaluator_version="1",
        evaluation_schema_version="1",
    )

    def __init__(self):
        self.requests = []

    def evaluate(self, request):
        self.requests.append(request)
        findings = []
        for axis in ContinuityAxis:
            supporting = [request.persona_context_refs[0].ref_id]
            reason_code = "aligned"
            assessment = "aligned"
            voice_activation_refs = []
            if axis == ContinuityAxis.VOICE_STYLE:
                reason_code = "supported_contextual_voice"
                assessment = "supported"
                supporting = [
                    next(
                        item.ref_id
                        for item in request.persona_context_refs
                        if item.kind
                        == ContinuityEvidenceKind.CONTEXTUAL_VOICE_PATTERN
                    )
                ]
                voice_activation_refs = [
                    request.voice_pattern_activations[0].activation_id
                ]
            findings.append(
                {
                    "finding_id": f"finding-{axis.value}",
                    "axis": axis.value,
                    "assessment": assessment,
                    "severity": "info",
                    "reason_code": reason_code,
                    "reply_start": 0,
                    "reply_end": 5,
                    "reply_quote": "Hello",
                    "supporting_basis_refs": supporting,
                    "conflicting_source_refs": [],
                    "voice_activation_refs": voice_activation_refs,
                }
            )
        return {"kind": "findings", "findings": findings}


class _PlainAlignedContinuityEvaluator:
    descriptor = ContinuityEvaluatorDescriptor(
        evaluator_id="tests.plain-continuity-evaluator",
        evaluator_version="1",
        evaluation_schema_version="1",
    )

    def __init__(self):
        self.requests = []

    def evaluate(self, request):
        self.requests.append(request)
        return {
            "kind": "findings",
            "findings": [
                {
                    "finding_id": f"plain-{axis.value}",
                    "axis": axis.value,
                    "assessment": "aligned",
                    "severity": "info",
                    "reason_code": "aligned",
                    "reply_start": 0,
                    "reply_end": len(request.proposed_reply),
                    "reply_quote": request.proposed_reply,
                    "supporting_basis_refs": [
                        request.persona_context_refs[0].ref_id
                    ],
                    "conflicting_source_refs": [],
                }
                for axis in ContinuityAxis
            ],
        }


class _InteractionContextEvaluator:
    descriptor = InteractionContextEvaluatorDescriptor(
        evaluator_id="tests.interaction-context-evaluator",
        evaluator_version="1",
    )

    def __init__(self, *, borrowed_evidence=False):
        self.borrowed_evidence = borrowed_evidence
        self.requests = []

    def evaluate(self, request):
        self.requests.append(request)
        evidence_ref = (
            "relationship-event:another-relationships-event"
            if self.borrowed_evidence
            else request.user_message_evidence_ref
        )
        return {
            "kind": "signals",
            "signals": [
                {
                    "candidate_key": "current-excitement",
                    "value": "excited",
                    "evidence_refs": [evidence_ref],
                }
            ],
        }


def _candidate():
    return {
        "schema_version": "0.4.0a7",
        "compiler_version": "tests.persona-compiler/1",
        "source_spans": [
            {
                "span_id": "span-playful",
                "start": 0,
                "end": 12,
                "quote": "Playful line",
            }
        ],
        "claims": [
            {
                "claim_id": "voice-playful",
                "kind": "voice",
                "statement": "She can sound playfully blunt while gaming.",
                "activation_tier": "situational",
                "basis": "explicit",
                "scope": "character",
                "source_span_ids": ["span-playful"],
            }
        ],
        "contextual_voice_patterns": [
            {
                "pattern_id": "playful-gaming",
                "description": "A concise and playfully blunt gaming register.",
                "scope": "character",
                "basis": "explicit",
                "source_span_ids": ["span-playful"],
                "conditions": [
                    {
                        "condition_id": "while-gaming",
                        "condition_type": "activity",
                        "values": ["gaming"],
                    }
                ],
                "required_claim_ids": ["voice-playful"],
            }
        ],
    }


def _persona_claim_ref(
    engine,
    *,
    agent_id="agent-lumi",
    user_id="user-chen",
    claim_id="voice-playful",
):
    manifest = engine.get_persona_manifest(agent_id, user_id)
    if manifest is None:
        raise AssertionError("test requires an approved Persona Manifest")
    return ContinuityEvidenceRef.create(
        ContinuityEvidenceKind.PERSONA_CLAIM,
        {
            "manifest_id": manifest.manifest_id,
            "content_fingerprint": manifest.content_fingerprint,
            "claim_id": claim_id,
        },
    )


def _persona_voice_pattern_ref(
    engine,
    *,
    agent_id="agent-lumi",
    user_id="user-chen",
    pattern_id="playful-gaming",
):
    manifest = engine.get_persona_manifest(agent_id, user_id)
    if manifest is None:
        raise AssertionError("test requires an approved Persona Manifest")
    return ContinuityEvidenceRef.create(
        ContinuityEvidenceKind.CONTEXTUAL_VOICE_PATTERN,
        {
            "manifest_id": manifest.manifest_id,
            "content_fingerprint": manifest.content_fingerprint,
            "pattern_id": pattern_id,
        },
    )


def _initialize_with_manifest(engine, *, user_id="user-chen"):
    engine.initialize_relationship(
        "agent-lumi",
        user_id,
        "Playful line",
    )
    proposal = engine.propose_persona_compilation(
        "agent-lumi",
        user_id,
        _candidate(),
    )
    engine.decide_persona_compilation(
        "agent-lumi",
        user_id,
        proposal.proposal_id,
        proposal.revision,
        "owner",
        "approve",
    )
    manifest = engine.get_persona_manifest("agent-lumi", user_id)
    if manifest is None:
        raise AssertionError("test requires an approved Persona Manifest")
    return manifest


def _derived_context_candidate():
    candidate = _candidate()
    candidate["contextual_voice_patterns"] = [
        {
            "pattern_id": "emotion-playful",
            "description": "A concise and playfully excited register.",
            "scope": "character",
            "basis": "explicit",
            "source_span_ids": ["span-playful"],
            "conditions": [
                {
                    "condition_id": "when-excited",
                    "condition_type": "emotion",
                    "values": ["excited"],
                }
            ],
            "required_claim_ids": ["voice-playful"],
        },
        {
            "pattern_id": "safety-playful",
            "description": "A relaxed register in a moderately safe relationship.",
            "scope": "character",
            "basis": "explicit",
            "source_span_ids": ["span-playful"],
            "conditions": [
                {
                    "condition_id": "when-moderately-safe",
                    "condition_type": "relationship_safety",
                    "values": ["moderate"],
                }
            ],
            "required_claim_ids": ["voice-playful"],
        },
    ]
    return candidate


def _prepare_pending_persona_growth(engine, *, suffix):
    source_turn_id = f"growth-source-{suffix}"
    message_id = f"{source_turn_id}-user"
    message = "I am trying to repair what happened between us."
    accepted = engine.adjudicate_relationship_candidates(
        "agent-lumi",
        "user-chen",
        {
            "turn_id": source_turn_id,
            "revision": "1",
            "extractor_version": "tests.relationship-extractor/1",
            "messages": [
                {
                    "source_id": message_id,
                    "revision": "1",
                    "role": "user",
                    "content": message,
                    "occurred_at": "2026-08-01T00:00:00+00:00",
                }
            ],
        },
        [
            {
                "candidate_key": f"repair-{suffix}",
                "event_type": "repair",
                "summary": "The user made a concrete attempt to repair the relationship.",
                "signal": {
                    "signal_type": "repair",
                    "strength": "strong",
                    "extraction_confidence": 0.95,
                    "interpretation_confidence": 0.95,
                },
                "evidence": [{"source_id": message_id, "quote": message}],
                "occurrence_key": None,
                "depends_on": [],
                "persona_reflection": (
                    "This repair makes me reconsider how I respond when boundaries "
                    "are respected."
                ),
                "growth_trigger": "pivotal",
            }
        ],
    )
    return engine.propose_persona_growth(
        "agent-lumi",
        "user-chen",
        {
            "intent_key": f"accept-repair-{suffix}",
            "review_id": f"growth-review-{suffix}",
            "statement": "I may choose to accept repair while preserving my boundaries.",
            "rationale": "A rule-confirmed pivotal repair event supports this proposal.",
            "proposed_changes": {
                "relationship_traits": {"accepts_repair": True}
            },
            "supporting_event_ids": [accepted.events[0].event_id],
            "trigger_kind": "pivotal",
        },
    )


def _prepare_reviewed_growth_turn(engine, *, suffix):
    engine.initialize_relationship(
        "agent-lumi",
        "user-chen",
        "Playful line",
        compiled_persona={
            "relationship_policy": {
                "version": "tests-pivotal-repair/1",
                "pivotal_signals": ["repair"],
            }
        },
    )
    manifest_proposal = engine.propose_persona_compilation(
        "agent-lumi",
        "user-chen",
        _candidate(),
    )
    engine.decide_persona_compilation(
        "agent-lumi",
        "user-chen",
        manifest_proposal.proposal_id,
        manifest_proposal.revision,
        "owner",
        "approve",
    )
    growth = _prepare_pending_persona_growth(engine, suffix=suffix)
    engine.decide_persona_growth_proposal(
        "agent-lumi",
        "user-chen",
        growth.proposal_id,
        growth.revision,
        "owner",
        "approve",
    )
    turn = engine.begin_turn(
        "agent-lumi",
        "user-chen",
        "Can this repair matter?",
        turn_id=f"turn-growth-race-{suffix}",
    )
    reply = "It can matter without erasing my boundaries."
    result = engine.evaluate_reply_continuity(
        "agent-lumi",
        "user-chen",
        turn.turn_id,
        reply,
        persona_context_refs=(_persona_claim_ref(engine),),
    )
    return growth, turn, reply, result


class _PauseInsideReviewedTransitionFileStorage(FileStorage):
    """Pauses the Turn CAS while production still owns the snapshot root lock."""

    def __init__(self, root_dir):
        self.reviewed_cas_entered = Event()
        self.release_reviewed_cas = Event()
        self._pause_lock = Lock()
        self._pause_consumed = False
        super().__init__(root_dir)

    def transition_turn_record(
        self,
        record,
        expected_status,
        expected_record_version,
    ):
        should_pause = False
        if (
            record.status == TurnStatus.COMPLETED
            and record.review_record is not None
            and record.review_record.kind.value == "reviewed"
        ):
            with self._pause_lock:
                if not self._pause_consumed:
                    self._pause_consumed = True
                    should_pause = True
        if should_pause:
            self.reviewed_cas_entered.set()
            if not self.release_reviewed_cas.wait(10):
                raise TimeoutError("reviewed Turn CAS test gate was not released")
        return super().transition_turn_record(
            record,
            expected_status,
            expected_record_version,
        )


class _GateBeforeReviewedTransitionFileStorage(FileStorage):
    """Pauses before production acquires its atomic snapshot root lock."""

    def __init__(self, root_dir):
        self.reviewed_transition_requested = Event()
        self.release_reviewed_transition = Event()
        self._pause_lock = Lock()
        self._pause_consumed = False
        super().__init__(root_dir)

    def transition_reviewed_turn_record(
        self,
        profile,
        record,
        context_baseline,
        expected_status,
        expected_record_version,
    ):
        should_pause = False
        with self._pause_lock:
            if not self._pause_consumed:
                self._pause_consumed = True
                should_pause = True
        if should_pause:
            self.reviewed_transition_requested.set()
            if not self.release_reviewed_transition.wait(10):
                raise TimeoutError("reviewed transition test gate was not released")
        return super().transition_reviewed_turn_record(
            profile,
            record,
            context_baseline,
            expected_status,
            expected_record_version,
        )


class _ObservedSnapshotLockFileStorage(FileStorage):
    """Exposes deterministic observations around root snapshot-lock acquisition."""

    def __init__(self, root_dir):
        self.snapshot_lock_attempted = Event()
        self.snapshot_lock_acquired = Event()
        self.observe_snapshot_lock = False
        super().__init__(root_dir)

    @contextmanager
    def _turn_context_snapshot_guard(self):
        observing = self.observe_snapshot_lock
        if observing:
            self.snapshot_lock_attempted.set()
        with super()._turn_context_snapshot_guard():
            if observing:
                self.snapshot_lock_acquired.set()
            yield


class _PauseInsideReviewedTransitionSQLiteStorage(SQLiteStorage):
    """Pauses an authority read after the reviewed write transaction begins."""

    def __init__(self, db_path):
        self.atomic_recheck_entered = Event()
        self.release_atomic_recheck = Event()
        self.pause_atomic_recheck = False
        self._pause_lock = Lock()
        self._pause_consumed = False
        super().__init__(db_path)

    def _capture_turn_context_source_with_connection(self, conn, profile):
        should_pause = False
        if self.pause_atomic_recheck:
            with self._pause_lock:
                if not self._pause_consumed:
                    self._pause_consumed = True
                    should_pause = True
        if should_pause:
            self.atomic_recheck_entered.set()
            if not self.release_atomic_recheck.wait(10):
                raise TimeoutError("SQLite atomic recheck test gate was not released")
        return super()._capture_turn_context_source_with_connection(conn, profile)


class _ObservedGrowthWriteSQLiteStorage(SQLiteStorage):
    """Signals when a competing Persona Growth write reaches SQLite storage."""

    def __init__(self, db_path):
        self.growth_write_attempted = Event()
        self.observe_growth_write = False
        super().__init__(db_path)

    def save_persona_growth_proposal(self, proposal, expected_status=None):
        if self.observe_growth_write:
            self.growth_write_attempted.set()
        return super().save_persona_growth_proposal(proposal, expected_status)


class ContinuityEnginePublicTests(unittest.TestCase):
    def test_engine_resolves_a_typed_manifest_claim_before_evaluation(self):
        with tempfile.TemporaryDirectory() as root:
            evaluator = _PlainAlignedContinuityEvaluator()
            with ERIIEngine(
                storage_dir=root,
                continuity_evaluator=evaluator,
            ) as engine:
                _initialize_with_manifest(engine)
                turn = engine.begin_turn(
                    "agent-lumi",
                    "user-chen",
                    "Want another game?",
                    turn_id="turn-typed-claim",
                )
                claim_ref = _persona_claim_ref(engine)

                result = engine.evaluate_reply_continuity(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                    "Yes. One more round.",
                    persona_context_refs=(claim_ref,),
                )

                self.assertEqual(len(evaluator.requests), 1)
                self.assertEqual(
                    evaluator.requests[0].persona_context_refs,
                    (claim_ref,),
                )
                self.assertEqual(
                    result.review_binding.persona_context_refs,
                    (claim_ref,),
                )

    def test_dangling_manifest_claim_fails_before_evaluator(self):
        with tempfile.TemporaryDirectory() as root:
            evaluator = _PlainAlignedContinuityEvaluator()
            with ERIIEngine(
                storage_dir=root,
                continuity_evaluator=evaluator,
            ) as engine:
                _initialize_with_manifest(engine)
                turn = engine.begin_turn(
                    "agent-lumi",
                    "user-chen",
                    "Want another game?",
                    turn_id="turn-dangling-claim",
                )
                dangling = _persona_claim_ref(
                    engine,
                    claim_id="missing-claim",
                )

                with self.assertRaisesRegex(ValueError, "dangling"):
                    engine.evaluate_reply_continuity(
                        "agent-lumi",
                        "user-chen",
                        turn.turn_id,
                        "Yes. One more round.",
                        persona_context_refs=(dangling,),
                    )

                self.assertEqual(evaluator.requests, [])
                self.assertEqual(
                    engine.get_turn(
                        "agent-lumi",
                        "user-chen",
                        turn.turn_id,
                    ).status,
                    TurnStatus.OPEN,
                )

    def test_cross_relationship_event_ref_fails_before_evaluator(self):
        with tempfile.TemporaryDirectory() as root:
            evaluator = _PlainAlignedContinuityEvaluator()
            with ERIIEngine(
                storage_dir=root,
                continuity_evaluator=evaluator,
            ) as engine:
                _initialize_with_manifest(engine)
                other_profile = engine.initialize_relationship(
                    "agent-lumi",
                    "user-other",
                    "Playful line",
                )
                other_event = engine.storage.append_relationship_event(
                    RelationshipEvent(
                        event_id="event-other-relationship",
                        relationship_id=other_profile.relationship_id,
                        event_type="shared_experience",
                        content="An event from another isolated relationship.",
                    )
                )
                turn = engine.begin_turn(
                    "agent-lumi",
                    "user-chen",
                    "What do you remember?",
                    turn_id="turn-cross-relationship-ref",
                )
                borrowed = ContinuityEvidenceRef.create(
                    ContinuityEvidenceKind.RELATIONSHIP_EVENT,
                    {
                        "relationship_id": other_profile.relationship_id,
                        "event_id": other_event.event_id,
                    },
                )

                with self.assertRaisesRegex(ValueError, "another relationship"):
                    engine.evaluate_reply_continuity(
                        "agent-lumi",
                        "user-chen",
                        turn.turn_id,
                        "I remember only what belongs to us.",
                        persona_context_refs=(_persona_claim_ref(engine),),
                        relationship_context_refs=(borrowed,),
                    )

                self.assertEqual(evaluator.requests, [])
                self.assertEqual(
                    engine.get_turn(
                        "agent-lumi",
                        "user-chen",
                        turn.turn_id,
                    ).status,
                    TurnStatus.OPEN,
                )

    def test_revoked_persona_growth_rejects_a_previously_successful_review(self):
        with tempfile.TemporaryDirectory() as root:
            with ERIIEngine(
                storage_dir=root,
                continuity_evaluator=_PlainAlignedContinuityEvaluator(),
            ) as engine:
                engine.initialize_relationship(
                    "agent-lumi",
                    "user-chen",
                    "Playful line",
                    compiled_persona={
                        "relationship_policy": {
                            "version": "tests-pivotal-repair/1",
                            "pivotal_signals": ["repair"],
                        }
                    },
                )
                manifest_proposal = engine.propose_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    _candidate(),
                )
                engine.decide_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    manifest_proposal.proposal_id,
                    manifest_proposal.revision,
                    "owner",
                    "approve",
                )
                growth = _prepare_pending_persona_growth(
                    engine,
                    suffix="revoke",
                )
                engine.decide_persona_growth_proposal(
                    "agent-lumi",
                    "user-chen",
                    growth.proposal_id,
                    growth.revision,
                    "owner",
                    "approve",
                )
                turn = engine.begin_turn(
                    "agent-lumi",
                    "user-chen",
                    "Can this repair matter?",
                    turn_id="turn-revoked-growth",
                )
                reply = "It can matter without erasing my boundaries."
                result = engine.evaluate_reply_continuity(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                    reply,
        persona_context_refs=(_persona_claim_ref(engine),),
                )
                self.assertEqual(
                    [item.proposal_id for item in turn.context_baseline.approved_growth_refs],
                    [growth.proposal_id],
                )
                engine.decide_persona_growth_proposal(
                    "agent-lumi",
                    "user-chen",
                    growth.proposal_id,
                    growth.revision,
                    "owner",
                    "revoke",
                    reason="authority withdrawn",
                )

                with self.assertRaisesRegex(ValueError, "Persona Growth.*no longer approved"):
                    engine.complete_turn(
                        "agent-lumi",
                        "user-chen",
                        turn.turn_id,
                        reply,
                        continuity_result=result,
                    )

                self.assertEqual(
                    engine.get_turn(
                        "agent-lumi",
                        "user-chen",
                        turn.turn_id,
                    ).status,
                    TurnStatus.OPEN,
                )

    def test_persona_growth_approved_after_opening_is_deferred(self):
        with tempfile.TemporaryDirectory() as root:
            with ERIIEngine(
                storage_dir=root,
                continuity_evaluator=_PlainAlignedContinuityEvaluator(),
            ) as engine:
                engine.initialize_relationship(
                    "agent-lumi",
                    "user-chen",
                    "Playful line",
                    compiled_persona={
                        "relationship_policy": {
                            "version": "tests-pivotal-repair/1",
                            "pivotal_signals": ["repair"],
                        }
                    },
                )
                manifest_proposal = engine.propose_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    _candidate(),
                )
                engine.decide_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    manifest_proposal.proposal_id,
                    manifest_proposal.revision,
                    "owner",
                    "approve",
                )
                growth = _prepare_pending_persona_growth(
                    engine,
                    suffix="deferred",
                )
                turn = engine.begin_turn(
                    "agent-lumi",
                    "user-chen",
                    "Can this repair matter?",
                    turn_id="turn-deferred-growth",
                )
                self.assertEqual(turn.context_baseline.approved_growth_refs, ())

                engine.decide_persona_growth_proposal(
                    "agent-lumi",
                    "user-chen",
                    growth.proposal_id,
                    growth.revision,
                    "owner",
                    "approve",
                )
                reply = "It can matter without rewriting this moment."
                result = engine.evaluate_reply_continuity(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                    reply,
        persona_context_refs=(_persona_claim_ref(engine),),
                )

                engine.complete_turn(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                    reply,
                    continuity_result=result,
                )
                completed = engine.get_turn(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                )
                self.assertEqual(completed.status, TurnStatus.COMPLETED)
                self.assertEqual(completed.context_baseline.approved_growth_refs, ())
                next_turn = engine.begin_turn(
                    "agent-lumi",
                    "user-chen",
                    "What about the next moment?",
                    turn_id="turn-after-growth-approval",
                )
                self.assertEqual(
                    [
                        item.proposal_id
                        for item in next_turn.context_baseline.approved_growth_refs
                    ],
                    [growth.proposal_id],
                )

    def test_reviewed_turn_cas_serializes_a_concurrent_growth_revocation(self):
        with tempfile.TemporaryDirectory() as root:
            completing_storage = _PauseInsideReviewedTransitionFileStorage(root)
            revoking_storage = _ObservedSnapshotLockFileStorage(root)
            with ERIIEngine(
                storage_driver=completing_storage,
                continuity_evaluator=_PlainAlignedContinuityEvaluator(),
            ) as completing_engine, ERIIEngine(
                storage_driver=revoking_storage,
            ) as revoking_engine:
                growth, turn, reply, result = _prepare_reviewed_growth_turn(
                    completing_engine,
                    suffix="cas-first",
                )

                def complete_reviewed_turn():
                    return completing_engine.complete_turn(
                        "agent-lumi",
                        "user-chen",
                        turn.turn_id,
                        reply,
                        continuity_result=result,
                    )

                def revoke_growth():
                    return revoking_engine.decide_persona_growth_proposal(
                        "agent-lumi",
                        "user-chen",
                        growth.proposal_id,
                        growth.revision,
                        "owner",
                        "revoke",
                        reason="authority withdrawn concurrently",
                    )

                with ThreadPoolExecutor(max_workers=2) as executor:
                    completion_future = executor.submit(complete_reviewed_turn)
                    try:
                        self.assertTrue(
                            completing_storage.reviewed_cas_entered.wait(5),
                            "reviewed Turn did not reach the paused CAS",
                        )
                        revoking_storage.snapshot_lock_attempted.clear()
                        revoking_storage.snapshot_lock_acquired.clear()
                        revoking_storage.observe_snapshot_lock = True
                        revoke_future = executor.submit(revoke_growth)
                        self.assertTrue(
                            revoking_storage.snapshot_lock_attempted.wait(5),
                            "Growth revocation did not attempt the snapshot root lock",
                        )
                        self.assertFalse(
                            revoking_storage.snapshot_lock_acquired.is_set(),
                            "Growth revocation entered the reviewed recheck/CAS window",
                        )
                    finally:
                        completing_storage.release_reviewed_cas.set()

                    completion_receipt = completion_future.result(timeout=5)
                    revoked = revoke_future.result(timeout=5)

                self.assertTrue(revoking_storage.snapshot_lock_acquired.is_set())
                self.assertEqual(revoked.status.value, "revoked")
                self.assertEqual(
                    completing_engine.get_turn(
                        "agent-lumi",
                        "user-chen",
                        turn.turn_id,
                    ).status,
                    TurnStatus.COMPLETED,
                )
                self.assertEqual(completion_receipt.source_turn_id, turn.turn_id)

    def test_sqlite_reviewed_turn_transaction_serializes_growth_revocation(self):
        with tempfile.TemporaryDirectory() as root:
            db_path = os.path.join(root, "continuity.db")
            completing_storage = _PauseInsideReviewedTransitionSQLiteStorage(
                db_path
            )
            revoking_storage = _ObservedGrowthWriteSQLiteStorage(db_path)
            with ERIIEngine(
                storage_driver=completing_storage,
                continuity_evaluator=_PlainAlignedContinuityEvaluator(),
            ) as completing_engine, ERIIEngine(
                storage_driver=revoking_storage,
            ) as revoking_engine:
                growth, turn, reply, result = _prepare_reviewed_growth_turn(
                    completing_engine,
                    suffix="sqlite-cas-first",
                )
                completing_storage.pause_atomic_recheck = True
                revoking_storage.observe_growth_write = True

                with ThreadPoolExecutor(max_workers=2) as executor:
                    completion_future = executor.submit(
                        completing_engine.complete_turn,
                        "agent-lumi",
                        "user-chen",
                        turn.turn_id,
                        reply,
                        continuity_result=result,
                    )
                    try:
                        self.assertTrue(
                            completing_storage.atomic_recheck_entered.wait(5),
                            "reviewed Turn did not enter its SQLite write transaction",
                        )
                        revoke_future = executor.submit(
                            revoking_engine.decide_persona_growth_proposal,
                            "agent-lumi",
                            "user-chen",
                            growth.proposal_id,
                            growth.revision,
                            "owner",
                            "revoke",
                            reason="authority withdrawn after SQLite Turn CAS",
                        )
                        self.assertTrue(
                            revoking_storage.growth_write_attempted.wait(5),
                            "Growth revocation did not reach SQLite storage",
                        )
                        self.assertFalse(
                            revoke_future.done(),
                            "Growth revocation crossed the SQLite recheck/CAS window",
                        )
                    finally:
                        completing_storage.release_atomic_recheck.set()

                    completion_receipt = completion_future.result(timeout=5)
                    revoked = revoke_future.result(timeout=5)

                self.assertEqual(completion_receipt.source_turn_id, turn.turn_id)
                self.assertEqual(revoked.status.value, "revoked")
                self.assertEqual(
                    completing_engine.get_turn(
                        "agent-lumi",
                        "user-chen",
                        turn.turn_id,
                    ).status,
                    TurnStatus.COMPLETED,
                )

    def test_sqlite_revoked_growth_prevents_reviewed_turn_completion(self):
        with tempfile.TemporaryDirectory() as root:
            storage = SQLiteStorage(os.path.join(root, "revoked-growth.db"))
            with ERIIEngine(
                storage_driver=storage,
                continuity_evaluator=_PlainAlignedContinuityEvaluator(),
            ) as engine:
                growth, turn, reply, result = _prepare_reviewed_growth_turn(
                    engine,
                    suffix="sqlite-revoke-first",
                )
                engine.decide_persona_growth_proposal(
                    "agent-lumi",
                    "user-chen",
                    growth.proposal_id,
                    growth.revision,
                    "owner",
                    "revoke",
                    reason="authority withdrawn before SQLite completion",
                )

                with self.assertRaisesRegex(
                    ValueError,
                    "Persona Growth.*no longer approved",
                ):
                    engine.complete_turn(
                        "agent-lumi",
                        "user-chen",
                        turn.turn_id,
                        reply,
                        continuity_result=result,
                    )

                stored = engine.get_turn(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                )
                self.assertEqual(stored.status, TurnStatus.OPEN)
                self.assertIsNone(stored.transcript.agent_message)

    def test_growth_revocation_before_atomic_recheck_keeps_the_turn_open(self):
        with tempfile.TemporaryDirectory() as root:
            completing_storage = _GateBeforeReviewedTransitionFileStorage(root)
            with ERIIEngine(
                storage_driver=completing_storage,
                continuity_evaluator=_PlainAlignedContinuityEvaluator(),
            ) as completing_engine, ERIIEngine(
                storage_driver=FileStorage(root),
            ) as revoking_engine:
                growth, turn, reply, result = _prepare_reviewed_growth_turn(
                    completing_engine,
                    suffix="revoke-first",
                )

                with ThreadPoolExecutor(max_workers=1) as executor:
                    completion_future = executor.submit(
                        completing_engine.complete_turn,
                        "agent-lumi",
                        "user-chen",
                        turn.turn_id,
                        reply,
                        continuity_result=result,
                    )
                    try:
                        self.assertTrue(
                            completing_storage.reviewed_transition_requested.wait(5),
                            "reviewed transition did not reach its pre-atomic gate",
                        )
                        revoked = revoking_engine.decide_persona_growth_proposal(
                            "agent-lumi",
                            "user-chen",
                            growth.proposal_id,
                            growth.revision,
                            "owner",
                            "revoke",
                            reason="authority withdrew before atomic recheck",
                        )
                        self.assertEqual(revoked.status.value, "revoked")
                    finally:
                        completing_storage.release_reviewed_transition.set()

                    with self.assertRaisesRegex(
                        ValueError,
                        "Persona Growth.*no longer approved",
                    ):
                        completion_future.result(timeout=5)

                stored = completing_engine.get_turn(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                )
                self.assertEqual(stored.status, TurnStatus.OPEN)
                self.assertIsNone(stored.transcript.agent_message)

    def test_completed_review_retry_survives_later_growth_revocation(self):
        with tempfile.TemporaryDirectory() as root:
            with ERIIEngine(
                storage_dir=root,
                continuity_evaluator=_PlainAlignedContinuityEvaluator(),
            ) as engine:
                growth, turn, reply, result = _prepare_reviewed_growth_turn(
                    engine,
                    suffix="retry-after-revoke",
                )
                first = engine.complete_turn(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                    reply,
                    continuity_result=result,
                )
                engine.decide_persona_growth_proposal(
                    "agent-lumi",
                    "user-chen",
                    growth.proposal_id,
                    growth.revision,
                    "owner",
                    "revoke",
                    reason="authority withdrawn after the Turn was sealed",
                )

                retried = engine.complete_turn(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                    reply,
                    continuity_result=result,
                )

                self.assertEqual(retried.to_dict(), first.to_dict())
                self.assertEqual(
                    engine.get_turn(
                        "agent-lumi",
                        "user-chen",
                        turn.turn_id,
                    ).status,
                    TurnStatus.COMPLETED,
                )

    def test_manifest_approved_after_opening_is_not_visible_to_that_turn(self):
        with tempfile.TemporaryDirectory() as root:
            with ERIIEngine(
                storage_dir=root,
                continuity_evaluator=_PlainAlignedContinuityEvaluator(),
            ) as engine:
                engine.initialize_relationship(
                    "agent-lumi",
                    "user-chen",
                    "Playful line",
                )
                turn = engine.begin_turn(
                    "agent-lumi",
                    "user-chen",
                    "Want another game?",
                    turn_id="turn-before-manifest",
                )
                proposal = engine.propose_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    _candidate(),
                )
                engine.decide_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    proposal.proposal_id,
                    proposal.revision,
                    "owner",
                    "approve",
                )

                with self.assertRaisesRegex(
                    PersonaManifestRequiredError,
                    "Turn Opening",
                ):
                    engine.evaluate_reply_continuity(
                        "agent-lumi",
                        "user-chen",
                        turn.turn_id,
                        "Yes. One more round.",
        persona_context_refs=(_persona_claim_ref(engine),),
                    )

    def test_manifest_revocation_rejects_a_previously_successful_review(self):
        with tempfile.TemporaryDirectory() as root:
            with ERIIEngine(
                storage_dir=root,
                continuity_evaluator=_PlainAlignedContinuityEvaluator(),
            ) as engine:
                engine.initialize_relationship(
                    "agent-lumi",
                    "user-chen",
                    "Playful line",
                )
                proposal = engine.propose_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    _candidate(),
                )
                engine.decide_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    proposal.proposal_id,
                    proposal.revision,
                    "owner",
                    "approve",
                )
                turn = engine.begin_turn(
                    "agent-lumi",
                    "user-chen",
                    "Want another game?",
                    turn_id="turn-revoked-manifest",
                )
                reply = "Yes. One more round."
                result = engine.evaluate_reply_continuity(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                    reply,
        persona_context_refs=(_persona_claim_ref(engine),),
                )
                engine.decide_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    proposal.proposal_id,
                    proposal.revision,
                    "owner",
                    "revoke",
                    reason="authority withdrawn",
                )

                with self.assertRaisesRegex(ValueError, "no longer approved"):
                    engine.complete_turn(
                        "agent-lumi",
                        "user-chen",
                        turn.turn_id,
                        reply,
                        continuity_result=result,
                    )

                self.assertEqual(
                    engine.get_turn(
                        "agent-lumi",
                        "user-chen",
                        turn.turn_id,
                    ).status,
                    TurnStatus.OPEN,
                )

    def test_complete_turn_persists_the_self_bound_review_result(self):
        with tempfile.TemporaryDirectory() as root:
            with ERIIEngine(
                storage_dir=root,
                continuity_evaluator=_PlainAlignedContinuityEvaluator(),
            ) as engine:
                engine.initialize_relationship(
                    "agent-lumi",
                    "user-chen",
                    "Playful line",
                )
                proposal = engine.propose_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    _candidate(),
                )
                engine.decide_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    proposal.proposal_id,
                    proposal.revision,
                    "owner",
                    "approve",
                )
                turn = engine.begin_turn(
                    "agent-lumi",
                    "user-chen",
                    "Want another game?",
                    turn_id="turn-reviewed-delivery",
                )
                reply = "Yes. One more round."
                result = engine.evaluate_reply_continuity(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                    reply,
        persona_context_refs=(_persona_claim_ref(engine),),
                )

                engine.complete_turn(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                    reply,
                    continuity_result=result,
                )
                completed = engine.get_turn(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                )

                self.assertEqual(completed.review_record.kind.value, "reviewed")
                self.assertEqual(
                    completed.review_record.receipt.review_binding,
                    result.review_binding,
                )
                self.assertEqual(
                    result.review_binding.context_baseline_fingerprint,
                    turn.context_baseline.baseline_fingerprint,
                )
                self.assertEqual(completed.continuity_assessment, result.assessment)

    def test_evaluation_is_pre_delivery_and_does_not_persist_the_draft(self):
        with tempfile.TemporaryDirectory() as root:
            evaluator = _ContinuityEvaluator()
            with ERIIEngine(
                storage_dir=root,
                continuity_evaluator=evaluator,
            ) as engine:
                engine.initialize_relationship(
                    "agent-lumi",
                    "user-chen",
                    "Playful line",
                )
                proposal = engine.propose_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    _candidate(),
                )
                engine.decide_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    proposal.proposal_id,
                    proposal.revision,
                    "owner",
                    "approve",
                )
                turn = engine.begin_turn(
                    "agent-lumi",
                    "user-chen",
                    "Want another game?",
                    turn_id="turn-game",
                    interaction_context=(
                        {
                            "signal_id": "activity-game",
                            "source": "host_observed",
                            "signal_type": "activity",
                            "value": "gaming",
                        },
                    ),
                )

                result = engine.evaluate_reply_continuity(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                    "Hello there.",
                    persona_context_refs=(
                        _persona_claim_ref(engine),
                        _persona_voice_pattern_ref(engine),
                    ),
                )

                self.assertEqual(
                    result.assessment.verdict,
                    ContinuityVerdict.ALIGNED,
                )
                self.assertEqual(len(result.voice_activation_traces), 1)
                self.assertEqual(
                    result.voice_activation_traces[0].pattern_ref_id,
                    _persona_voice_pattern_ref(engine).ref_id,
                )
                self.assertEqual(len(evaluator.requests), 1)
                still_open = engine.get_turn(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                )
                self.assertEqual(still_open.status, TurnStatus.OPEN)
                self.assertIsNone(still_open.transcript.agent_message)

                trace = result.voice_activation_traces[0]
                forged_match = replace(
                    trace.condition_matches[0],
                    source_context={
                        "kind": "host_observed",
                        "observation_fingerprint": "f" * 64,
                    },
                )
                forged_trace = VoiceActivationTrace.create(
                    activation_id=trace.activation_id,
                    relationship_id=trace.relationship_id,
                    turn_id=trace.turn_id,
                    persona_id=trace.persona_id,
                    manifest_id=trace.manifest_id,
                    context_baseline_fingerprint=(
                        trace.context_baseline_fingerprint
                    ),
                    pattern_ref_id=trace.pattern_ref_id,
                    pattern_scope=trace.pattern_scope,
                    matcher_version=trace.matcher_version,
                    matcher_input_fingerprint=trace.matcher_input_fingerprint,
                    condition_matches=(forged_match,),
                )
                forged_result = replace(
                    result,
                    voice_activation_traces=(forged_trace,),
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "does not match its parent Turn",
                ):
                    engine.complete_turn(
                        "agent-lumi",
                        "user-chen",
                        turn.turn_id,
                        "Hello there.",
                        continuity_result=forged_result,
                    )
                self.assertEqual(
                    engine.get_turn(
                        "agent-lumi",
                        "user-chen",
                        turn.turn_id,
                    ).status,
                    TurnStatus.OPEN,
                )

                engine.complete_turn(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                    "Hello there.",
                    continuity_result=result,
                )
                completed = engine.get_turn(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                )
                self.assertEqual(completed.status, TurnStatus.COMPLETED)
                self.assertEqual(
                    completed.continuity_assessment,
                    result.assessment,
                )

    def test_relationship_events_after_opening_are_deferred_to_the_next_turn(self):
        with tempfile.TemporaryDirectory() as root:
            evaluator = _InteractionContextEvaluator()
            with ERIIEngine(
                storage_dir=root,
                interaction_context_evaluator=evaluator,
            ) as engine:
                engine.initialize_relationship(
                    "agent-lumi",
                    "user-chen",
                    "Playful line",
                )
                proposal = engine.propose_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    _derived_context_candidate(),
                )
                engine.decide_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    proposal.proposal_id,
                    proposal.revision,
                    "owner",
                    "approve",
                )
                turn = engine.begin_turn(
                    "agent-lumi",
                    "user-chen",
                    "Want another game?",
                    turn_id="turn-frozen-history",
                )
                engine.record_relationship_event(
                    "agent-lumi",
                    "user-chen",
                    "observation",
                    "A later event made the relationship feel safer.",
                    state_delta={"safety": 0.1},
                    event_id="event-after-turn-opened-1",
                )
                engine.record_relationship_event(
                    "agent-lumi",
                    "user-chen",
                    "observation",
                    "Another later event also increased safety.",
                    state_delta={"safety": 0.1},
                    event_id="event-after-turn-opened-2",
                )

                activations = engine.activate_contextual_voice_patterns(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                )

                self.assertEqual(
                    {item.pattern_id for item in activations},
                    {"emotion-playful", "safety-playful"},
                )
                self.assertEqual(len(evaluator.requests), 1)
                self.assertEqual(evaluator.requests[0].relationship_state["safety"], 0.5)
                self.assertEqual(evaluator.requests[0].recent_events, ())

    def test_core_derived_voice_trace_replays_the_frozen_history_prefix(self):
        with tempfile.TemporaryDirectory() as root:
            storage_cases = (
                ("file", FileStorage(os.path.join(root, "file"))),
                ("sqlite", SQLiteStorage(os.path.join(root, "trace.db"))),
            )
            for storage_name, storage in storage_cases:
                with self.subTest(storage=storage_name), ERIIEngine(
                    storage_driver=storage,
                    continuity_evaluator=_ContinuityEvaluator(),
                ) as engine:
                    engine.initialize_relationship(
                        "agent-lumi",
                        "user-chen",
                        "Playful line",
                    )
                    proposal = engine.propose_persona_compilation(
                        "agent-lumi",
                        "user-chen",
                        _derived_context_candidate(),
                    )
                    engine.decide_persona_compilation(
                        "agent-lumi",
                        "user-chen",
                        proposal.proposal_id,
                        proposal.revision,
                        "owner",
                        "approve",
                    )
                    turn = engine.begin_turn(
                        "agent-lumi",
                        "user-chen",
                        "Are we all right?",
                        turn_id="turn-core-derived-trace",
                    )
                    result = engine.evaluate_reply_continuity(
                        "agent-lumi",
                        "user-chen",
                        turn.turn_id,
                        "Hello",
                        persona_context_refs=(
                            _persona_claim_ref(engine),
                            _persona_voice_pattern_ref(
                                engine,
                                pattern_id="safety-playful",
                            ),
                        ),
                    )
                    match = result.voice_activation_traces[0].condition_matches[0]
                    self.assertEqual(match.signal_source.value, "core_derived")
                    self.assertEqual(
                        match.source_context["history_prefix_fingerprint"],
                        turn.context_baseline.history_prefix_fingerprint,
                    )

                    engine.record_relationship_event(
                        "agent-lumi",
                        "user-chen",
                        "observation",
                        "A later event changes the live projection.",
                        state_delta={"safety": 0.1},
                        event_id="event-after-core-derived-trace",
                    )
                    engine.complete_turn(
                        "agent-lumi",
                        "user-chen",
                        turn.turn_id,
                        "Hello",
                        continuity_result=result,
                    )
                    self.assertEqual(
                        engine.get_turn(
                            "agent-lumi",
                            "user-chen",
                            turn.turn_id,
                        ).status,
                        TurnStatus.COMPLETED,
                    )

    def test_evaluator_voice_trace_completes_without_resampling_context(self):
        with tempfile.TemporaryDirectory() as root:
            context_evaluator = _InteractionContextEvaluator()
            with ERIIEngine(
                storage_dir=root,
                interaction_context_evaluator=context_evaluator,
                continuity_evaluator=_ContinuityEvaluator(),
            ) as engine:
                engine.initialize_relationship(
                    "agent-lumi",
                    "user-chen",
                    "Playful line",
                )
                proposal = engine.propose_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    _derived_context_candidate(),
                )
                engine.decide_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    proposal.proposal_id,
                    proposal.revision,
                    "owner",
                    "approve",
                )
                turn = engine.begin_turn(
                    "agent-lumi",
                    "user-chen",
                    "One more game!",
                    turn_id="turn-evaluator-trace",
                )
                result = engine.evaluate_reply_continuity(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                    "Hello",
                    persona_context_refs=(
                        _persona_claim_ref(engine),
                        _persona_voice_pattern_ref(
                            engine,
                            pattern_id="emotion-playful",
                        ),
                    ),
                )
                match = result.voice_activation_traces[0].condition_matches[0]
                self.assertEqual(match.signal_source.value, "evaluator_inferred")
                self.assertEqual(len(context_evaluator.requests), 1)

                engine.complete_turn(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                    "Hello",
                    continuity_result=result,
                )
                self.assertEqual(len(context_evaluator.requests), 1)

    def test_host_context_cannot_be_added_after_turn_opening(self):
        with tempfile.TemporaryDirectory() as root:
            with ERIIEngine(storage_dir=root) as engine:
                engine.initialize_relationship(
                    "agent-lumi",
                    "user-chen",
                    "Playful line",
                )
                proposal = engine.propose_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    _candidate(),
                )
                engine.decide_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    proposal.proposal_id,
                    proposal.revision,
                    "owner",
                    "approve",
                )
                turn = engine.begin_turn(
                    "agent-lumi",
                    "user-chen",
                    "Want another game?",
                    turn_id="turn-no-late-context",
                )

                with self.assertRaisesRegex(ValueError, "begin_turn"):
                    engine.activate_contextual_voice_patterns(
                        "agent-lumi",
                        "user-chen",
                        turn.turn_id,
                        interaction_context=(
                            {
                                "signal_id": "late-gaming-context",
                                "source": "host_observed",
                                "signal_type": "activity",
                                "value": "gaming",
                            },
                        ),
                    )

    def test_scoped_internal_context_producers_activate_and_cache_per_turn(self):
        with tempfile.TemporaryDirectory() as root:
            evaluator = _InteractionContextEvaluator()
            with ERIIEngine(
                storage_dir=root,
                interaction_context_evaluator=evaluator,
            ) as engine:
                profile = engine.initialize_relationship(
                    "agent-lumi",
                    "user-chen",
                    "Playful line",
                )
                proposal = engine.propose_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    _derived_context_candidate(),
                )
                engine.decide_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    proposal.proposal_id,
                    proposal.revision,
                    "owner",
                    "approve",
                )
                turn = engine.begin_turn(
                    "agent-lumi",
                    "user-chen",
                    "Let's go outside!",
                    turn_id="turn-excited",
                    interaction_context=(
                        {
                            "signal_id": "current-location",
                            "source": "host_observed",
                            "signal_type": "location",
                            "value": "street",
                            "recorded_at": "2026-07-29T00:00:00+00:00",
                        },
                    ),
                )

                first = engine.activate_contextual_voice_patterns(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                )
                repeated = engine.activate_contextual_voice_patterns(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                )

                self.assertEqual(first, repeated)
                self.assertEqual(
                    {item.pattern_id for item in first},
                    {"emotion-playful", "safety-playful"},
                )
                self.assertTrue(
                    all(
                        item.relationship_id == profile.relationship_id
                        and item.source_turn_id == turn.turn_id
                        for item in first
                    )
                )
                self.assertEqual(len(evaluator.requests), 1)
                self.assertEqual(
                    evaluator.requests[0].relationship_id,
                    profile.relationship_id,
                )
                self.assertEqual(
                    evaluator.requests[0].turn_id,
                    turn.turn_id,
                )
                self.assertEqual(len(engine._interaction_context_cache), 1)
                engine.abandon_turn(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                    reason="test complete",
                )
                self.assertEqual(len(engine._interaction_context_cache), 0)

    def test_context_evaluator_cannot_borrow_another_relationships_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            evaluator = _InteractionContextEvaluator(borrowed_evidence=True)
            with ERIIEngine(
                storage_dir=root,
                interaction_context_evaluator=evaluator,
            ) as engine:
                engine.initialize_relationship(
                    "agent-lumi",
                    "user-chen",
                    "Playful line",
                )
                proposal = engine.propose_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    _derived_context_candidate(),
                )
                engine.decide_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    proposal.proposal_id,
                    proposal.revision,
                    "owner",
                    "approve",
                )
                turn = engine.begin_turn(
                    "agent-lumi",
                    "user-chen",
                    "Let's go outside!",
                    turn_id="turn-excited",
                )

                with self.assertRaisesRegex(
                    ValueError,
                    "current relationship/Turn",
                ):
                    engine.activate_contextual_voice_patterns(
                        "agent-lumi",
                        "user-chen",
                        turn.turn_id,
                    )

    def test_host_entrypoints_reject_unscopeable_derived_context_signals(self):
        with tempfile.TemporaryDirectory() as root:
            with ERIIEngine(storage_dir=root) as engine:
                engine.initialize_relationship(
                    "agent-lumi",
                    "user-chen",
                    "Playful line",
                )
                proposal = engine.propose_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    _candidate(),
                )
                engine.decide_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    proposal.proposal_id,
                    proposal.revision,
                    "owner",
                    "approve",
                )

                with self.assertRaisesRegex(
                    ValueError,
                    "host_observed",
                ):
                    engine.begin_turn(
                        "agent-lumi",
                        "user-chen",
                        "Want another game?",
                        turn_id="turn-spoofed-core",
                        interaction_context=(
                            {
                                "signal_id": "borrowed-safety",
                                "source": "core_derived",
                                "signal_type": "relationship_safety",
                                "value": "safe",
                                "evidence_refs": ["relationship:someone-else"],
                            },
                        ),
                    )

                with self.assertRaisesRegex(
                    ValueError,
                    "cannot set internal",
                ):
                    engine.begin_turn(
                        "agent-lumi",
                        "user-chen",
                        "Want another game?",
                        turn_id="turn-spoofed-host-scope",
                        interaction_context=(
                            {
                                "signal_id": "scoped-location",
                                "source": "host_observed",
                                "signal_type": "location",
                                "value": "street",
                                "relationship_id": "borrowed-relationship",
                                "source_turn_id": "borrowed-turn",
                                "producer_version": "spoofed/1",
                            },
                        ),
                    )

                turn = engine.begin_turn(
                    "agent-lumi",
                    "user-chen",
                    "Want another game?",
                    turn_id="turn-game",
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "host_observed",
                ):
                    engine.activate_contextual_voice_patterns(
                        "agent-lumi",
                        "user-chen",
                        turn.turn_id,
                        interaction_context=(
                            {
                                "signal_id": "borrowed-emotion",
                                "source": "evaluator_inferred",
                                "signal_type": "emotion",
                                "value": "excited",
                                "evidence_refs": ["turn:another-relationship"],
                            },
                        ),
                    )


if __name__ == "__main__":
    unittest.main()
