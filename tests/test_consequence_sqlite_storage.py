"""SQLite contracts for the v0.5 relationship-consequence journal."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import os
import sqlite3
import tempfile
import threading
import unittest

from erii.models.consequence import (
    ConsequenceConflictError,
    NarrativeTensionConflictError,
    NarrativeTensionLink,
    NarrativeTensionOutcome,
    RelationshipConsequence,
    RelationshipConsequenceKind,
)
from erii.storage.sqlite_storage import SQLiteStorage


class SQLiteConsequenceStorageTests(unittest.TestCase):
    @staticmethod
    def _seed_relationship(storage: SQLiteStorage, suffix: str = "one") -> str:
        relationship_id = f"relationship-{suffix}"
        with closing(sqlite3.connect(storage.db_path)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(
                """
                INSERT INTO stable_identities (
                    identity_id, kind, external_id, created_at
                ) VALUES (?, ?, ?, ?), (?, ?, ?, ?)
                """,
                (
                    f"agent-identity-{suffix}",
                    "agent",
                    f"agent-{suffix}",
                    "2026-08-06T00:00:00+00:00",
                    f"user-identity-{suffix}",
                    "user",
                    f"user-{suffix}",
                    "2026-08-06T00:00:00+00:00",
                ),
            )
            connection.execute(
                """
                INSERT INTO relationships (
                    relationship_id, persona_id, blueprint_id,
                    agent_identity_id, user_identity_id, agent_id, user_id,
                    blueprint_data, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    relationship_id,
                    f"persona-{suffix}",
                    f"blueprint-{suffix}",
                    f"agent-identity-{suffix}",
                    f"user-identity-{suffix}",
                    f"agent-{suffix}",
                    f"user-{suffix}",
                    "{}",
                    "2026-08-06T00:00:00+00:00",
                ),
            )
            connection.commit()
        return relationship_id

    @staticmethod
    def _consequence(
        relationship_id: str,
        *,
        consequence_id: str = "consequence-one",
        tension_id: str = "tension-one",
        decision_id: str = "decision-one",
        event_id: str = "event-one",
        summary: str = "A refusal left an explicit unresolved tension.",
        recorded_at: str = "2026-08-06T01:00:00+00:00",
    ) -> RelationshipConsequence:
        return RelationshipConsequence(
            consequence_id=consequence_id,
            relationship_id=relationship_id,
            tension_id=tension_id,
            source_turn_id=f"turn-{event_id}",
            source_revision=f"revision-{event_id}",
            source_decision_id=decision_id,
            source_event_id=event_id,
            source_message_id=f"message-{event_id}",
            effects=(RelationshipConsequenceKind.REFUSAL,),
            summary=summary,
            recorded_at=recorded_at,
        )

    @staticmethod
    def _link(
        relationship_id: str,
        *,
        link_id: str = "link-one",
        consequence_id: str = "consequence-one",
        tension_id: str = "tension-one",
        decision_id: str = "decision-link-one",
        event_id: str = "event-link-one",
        summary: str = "The refusal was addressed but remains unresolved.",
        recorded_at: str = "2026-08-06T02:00:00+00:00",
    ) -> NarrativeTensionLink:
        return NarrativeTensionLink(
            link_id=link_id,
            relationship_id=relationship_id,
            tension_id=tension_id,
            consequence_id=consequence_id,
            source_turn_id=f"turn-{event_id}",
            source_revision=f"revision-{event_id}",
            source_decision_id=decision_id,
            source_event_id=event_id,
            outcome=NarrativeTensionOutcome.ADDRESSED_UNRESOLVED,
            summary=summary,
            recorded_at=recorded_at,
        )

    def test_round_trip_order_and_retry_preserve_first_recorded_time(self):
        with tempfile.TemporaryDirectory() as root:
            storage = SQLiteStorage(os.path.join(root, "memory.db"))
            relationship_id = self._seed_relationship(storage)
            first = self._consequence(relationship_id)
            second = self._consequence(
                relationship_id,
                consequence_id="consequence-two",
                tension_id="tension-two",
                decision_id="decision-two",
                event_id="event-two",
            )

            self.assertEqual(storage.append_relationship_consequence(first), first)
            retried = storage.append_relationship_consequence(
                replace(first, recorded_at="2026-08-06T09:00:00+00:00")
            )
            self.assertEqual(retried, first)
            storage.append_relationship_consequence(second)
            self.assertEqual(
                storage.list_relationship_consequences(relationship_id),
                [first, second],
            )

            first_link = self._link(relationship_id)
            second_link = self._link(
                relationship_id,
                link_id="link-two",
                consequence_id="consequence-two",
                tension_id="tension-two",
                decision_id="decision-link-two",
                event_id="event-link-two",
            )
            self.assertEqual(
                storage.append_narrative_tension_link(first_link),
                first_link,
            )
            retried_link = storage.append_narrative_tension_link(
                replace(
                    first_link,
                    recorded_at="2026-08-06T10:00:00+00:00",
                )
            )
            self.assertEqual(retried_link, first_link)
            storage.append_narrative_tension_link(second_link)
            self.assertEqual(
                storage.list_narrative_tension_links(relationship_id),
                [first_link, second_link],
            )

    def test_ids_and_source_identities_are_immutable(self):
        with tempfile.TemporaryDirectory() as root:
            storage = SQLiteStorage(os.path.join(root, "memory.db"))
            relationship_id = self._seed_relationship(storage)
            consequence = self._consequence(relationship_id)
            storage.append_relationship_consequence(consequence)

            with self.assertRaises(ConsequenceConflictError):
                storage.append_relationship_consequence(
                    replace(consequence, summary="Conflicting ID payload.")
                )
            with self.assertRaises(ConsequenceConflictError):
                storage.append_relationship_consequence(
                    replace(consequence, consequence_id="different-id")
                )

            link = self._link(relationship_id)
            storage.append_narrative_tension_link(link)
            with self.assertRaises(NarrativeTensionConflictError):
                storage.append_narrative_tension_link(
                    replace(link, summary="Conflicting link ID payload.")
                )
            with self.assertRaises(NarrativeTensionConflictError):
                storage.append_narrative_tension_link(
                    replace(link, link_id="different-link-id")
                )

    def test_link_requires_matching_relationship_consequence_and_tension(self):
        with tempfile.TemporaryDirectory() as root:
            storage = SQLiteStorage(os.path.join(root, "memory.db"))
            relationship_id = self._seed_relationship(storage, "one")
            other_relationship_id = self._seed_relationship(storage, "two")
            storage.append_relationship_consequence(
                self._consequence(relationship_id)
            )

            invalid_links = (
                self._link(
                    relationship_id,
                    consequence_id="missing-consequence",
                ),
                self._link(relationship_id, tension_id="different-tension"),
                self._link(other_relationship_id),
            )
            for invalid_link in invalid_links:
                with self.subTest(link=invalid_link):
                    with self.assertRaises(NarrativeTensionConflictError):
                        storage.append_narrative_tension_link(invalid_link)

            with self.assertRaisesRegex(ValueError, "unknown relationship"):
                storage.append_relationship_consequence(
                    self._consequence("missing-relationship")
                )
            with self.assertRaisesRegex(ValueError, "unknown relationship"):
                storage.append_narrative_tension_link(
                    self._link("missing-relationship")
                )

    def test_foreign_keys_and_relationship_delete_cascade(self):
        with tempfile.TemporaryDirectory() as root:
            storage = SQLiteStorage(os.path.join(root, "memory.db"))
            relationship_id = self._seed_relationship(storage)
            storage.append_relationship_consequence(
                self._consequence(relationship_id)
            )
            storage.append_narrative_tension_link(self._link(relationship_id))

            with closing(sqlite3.connect(storage.db_path)) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO narrative_tension_links (
                            link_id, relationship_id, tension_id, consequence_id,
                            source_decision_id, source_event_id, data, recorded_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "orphan-link",
                            relationship_id,
                            "orphan-tension",
                            "orphan-consequence",
                            "orphan-decision",
                            "orphan-event",
                            "{}",
                            "2026-08-06T03:00:00+00:00",
                        ),
                    )
                connection.rollback()
                connection.execute(
                    "DELETE FROM relationships WHERE relationship_id = ?",
                    (relationship_id,),
                )
                connection.commit()
                consequence_count = connection.execute(
                    "SELECT COUNT(*) FROM relationship_consequences"
                ).fetchone()[0]
                link_count = connection.execute(
                    "SELECT COUNT(*) FROM narrative_tension_links"
                ).fetchone()[0]

            self.assertEqual(consequence_count, 0)
            self.assertEqual(link_count, 0)

    def test_cross_instance_source_identity_race_returns_domain_conflict(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "memory.db")
            first_storage = SQLiteStorage(path)
            second_storage = SQLiteStorage(path)
            relationship_id = self._seed_relationship(first_storage)
            first = self._consequence(relationship_id)
            second = replace(
                first,
                consequence_id="consequence-racing",
                summary="Conflicting concurrent payload.",
            )
            barrier = threading.Barrier(2)

            def append(
                storage: SQLiteStorage,
                consequence: RelationshipConsequence,
            ) -> RelationshipConsequence | ConsequenceConflictError:
                barrier.wait(timeout=5)
                try:
                    return storage.append_relationship_consequence(consequence)
                except ConsequenceConflictError as exc:
                    return exc

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = tuple(
                    future.result(timeout=10)
                    for future in (
                        pool.submit(append, first_storage, first),
                        pool.submit(append, second_storage, second),
                    )
                )

            self.assertEqual(
                sum(isinstance(item, RelationshipConsequence) for item in results),
                1,
            )
            self.assertEqual(
                sum(isinstance(item, ConsequenceConflictError) for item in results),
                1,
            )
            self.assertEqual(
                len(first_storage.list_relationship_consequences(relationship_id)),
                1,
            )


if __name__ == "__main__":
    unittest.main()
