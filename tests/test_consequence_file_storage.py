"""FileStorage contracts for append-only relationship consequences."""

from dataclasses import replace
import tempfile
import unittest

from erii import ERIIEngine, FileStorage
from erii.models.consequence import (
    ConsequenceConflictError,
    NarrativeTensionConflictError,
    NarrativeTensionLink,
    NarrativeTensionOutcome,
    RelationshipConsequence,
    RelationshipConsequenceKind,
)


class ConsequenceFileStorageTest(unittest.TestCase):
    def _relationship(self, root_dir: str):
        storage = FileStorage(root_dir)
        with ERIIEngine(storage_driver=storage) as engine:
            profile = engine.initialize_relationship(
                "agent_lumi",
                "user_chen",
                "Lumi accepts that meaningful choices can leave consequences.",
            )
        return storage, profile

    @staticmethod
    def _consequence(
        relationship_id: str,
        *,
        consequence_id: str = "consequence-1",
        tension_id: str = "tension-1",
        source_event_id: str = "event-1",
        summary: str = "The refusal created distance.",
        recorded_at: str = "2026-08-06T00:00:00+00:00",
    ) -> RelationshipConsequence:
        return RelationshipConsequence(
            consequence_id=consequence_id,
            relationship_id=relationship_id,
            tension_id=tension_id,
            source_turn_id="turn-1",
            source_revision="1",
            source_decision_id="decision-1",
            source_event_id=source_event_id,
            source_message_id="message-1",
            effects=(RelationshipConsequenceKind.TEMPORARY_DISTANCE,),
            summary=summary,
            recorded_at=recorded_at,
        )

    @staticmethod
    def _link(
        relationship_id: str,
        *,
        link_id: str = "link-1",
        consequence_id: str = "consequence-1",
        tension_id: str = "tension-1",
        source_event_id: str = "event-2",
        summary: str = "The distance was acknowledged but remains unresolved.",
        recorded_at: str = "2026-08-06T00:01:00+00:00",
    ) -> NarrativeTensionLink:
        return NarrativeTensionLink(
            link_id=link_id,
            relationship_id=relationship_id,
            tension_id=tension_id,
            consequence_id=consequence_id,
            source_turn_id="turn-2",
            source_revision="1",
            source_decision_id="decision-2",
            source_event_id=source_event_id,
            outcome=NarrativeTensionOutcome.ADDRESSED_UNRESOLVED,
            summary=summary,
            recorded_at=recorded_at,
        )

    def test_consequences_persist_in_append_order_and_retry_idempotently(self):
        with tempfile.TemporaryDirectory() as root_dir:
            storage, profile = self._relationship(root_dir)
            first = self._consequence(profile.relationship_id)
            second = self._consequence(
                profile.relationship_id,
                consequence_id="consequence-2",
                tension_id="tension-2",
                source_event_id="event-2",
                summary="The boundary remained explicit.",
                recorded_at="2026-08-06T00:00:01+00:00",
            )

            self.assertEqual(storage.append_relationship_consequence(first), first)
            retry = replace(first, recorded_at="2026-08-06T00:00:09+00:00")
            self.assertEqual(storage.append_relationship_consequence(retry), first)
            storage.append_relationship_consequence(second)

            reopened = FileStorage(root_dir)
            self.assertEqual(
                reopened.list_relationship_consequences(profile.relationship_id),
                [first, second],
            )

    def test_consequence_id_conflict_and_unknown_relationship_are_rejected(self):
        with tempfile.TemporaryDirectory() as root_dir:
            storage, profile = self._relationship(root_dir)
            consequence = self._consequence(profile.relationship_id)
            storage.append_relationship_consequence(consequence)

            with self.assertRaises(ConsequenceConflictError):
                storage.append_relationship_consequence(
                    replace(consequence, summary="Conflicting durable content.")
                )
            with self.assertRaises(ValueError):
                storage.append_relationship_consequence(
                    self._consequence("unknown-relationship")
                )

    def test_tension_links_require_the_exact_existing_consequence(self):
        with tempfile.TemporaryDirectory() as root_dir:
            storage, profile = self._relationship(root_dir)
            relationship_id = profile.relationship_id
            consequence = self._consequence(relationship_id)
            storage.append_relationship_consequence(consequence)

            with self.assertRaises(NarrativeTensionConflictError):
                storage.append_narrative_tension_link(
                    self._link(
                        relationship_id,
                        consequence_id="missing-consequence",
                    )
                )
            with self.assertRaises(NarrativeTensionConflictError):
                storage.append_narrative_tension_link(
                    self._link(relationship_id, tension_id="missing-tension")
                )

    def test_tension_links_are_ordered_idempotent_and_source_unique(self):
        with tempfile.TemporaryDirectory() as root_dir:
            storage, profile = self._relationship(root_dir)
            relationship_id = profile.relationship_id
            consequence = self._consequence(relationship_id)
            storage.append_relationship_consequence(consequence)
            first = self._link(relationship_id)
            second = self._link(
                relationship_id,
                link_id="link-2",
                source_event_id="event-3",
                summary="A later exchange stabilized the boundary.",
                recorded_at="2026-08-06T00:02:00+00:00",
            )

            self.assertEqual(storage.append_narrative_tension_link(first), first)
            retry = replace(first, recorded_at="2026-08-06T00:01:09+00:00")
            self.assertEqual(storage.append_narrative_tension_link(retry), first)
            with self.assertRaises(NarrativeTensionConflictError):
                storage.append_narrative_tension_link(
                    replace(first, summary="Conflicting durable content.")
                )
            with self.assertRaises(NarrativeTensionConflictError):
                storage.append_narrative_tension_link(
                    replace(first, link_id="different-link-id")
                )

            storage.append_narrative_tension_link(second)
            reopened = FileStorage(root_dir)
            self.assertEqual(
                reopened.list_narrative_tension_links(relationship_id),
                [first, second],
            )


if __name__ == "__main__":
    unittest.main()
