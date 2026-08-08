"""Cross-version contracts for the MemoryPack 0.5.0a1 wire boundary."""

import json
from pathlib import Path
import unittest

from erii import MemoryPack
from erii.models.consequence import (
    RelationshipConsequence,
    RelationshipConsequenceKind,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "docs" / "contracts"
LEGACY_VERSION = "0.4.0a8"
CURRENT_VERSION = "0.5.0a2"
V050A1_EXTENSION_FIELDS = {
    "relationship_consequences",
    "narrative_tension_links",
}


def _root_fields(release: str) -> set[str]:
    contract_path = CONTRACTS / f"v{release}-data-formats.json"
    document = json.loads(contract_path.read_text(encoding="utf-8"))
    return set(document["memory_pack_envelope"]["root_fields"])


class MemoryPackWireVersionTests(unittest.TestCase):
    def test_v050a1_writer_emits_the_new_wire_fields(self) -> None:
        document = MemoryPack(agent_id="agent", user_id="user").to_dict()

        self.assertEqual(document["metadata"]["version"], CURRENT_VERSION)
        self.assertEqual(document["relationship_consequences"], [])
        self.assertEqual(document["narrative_tension_links"], [])

    def test_v050a1_reader_accepts_and_preserves_a_v040a8_envelope(self) -> None:
        legacy_document = MemoryPack(
            agent_id="agent",
            user_id="user",
            version=LEGACY_VERSION,
        ).to_dict()

        self.assertEqual(set(legacy_document), _root_fields("0.4.0"))
        self.assertTrue(V050A1_EXTENSION_FIELDS.isdisjoint(legacy_document))

        restored = MemoryPack.from_dict(legacy_document)

        self.assertEqual(restored.version, LEGACY_VERSION)
        self.assertEqual(restored.relationship_consequences, [])
        self.assertEqual(restored.narrative_tension_links, [])
        self.assertTrue(V050A1_EXTENSION_FIELDS.isdisjoint(restored.to_dict()))

    def test_v040a8_label_rejects_v050a1_extension_fields(self) -> None:
        mislabeled_document = MemoryPack(
            agent_id="agent",
            user_id="user",
        ).to_dict()
        mislabeled_document["metadata"]["version"] = LEGACY_VERSION

        with self.assertRaisesRegex(
            ValueError,
            "fields introduced in '0.5.0a1'",
        ):
            MemoryPack.from_dict(mislabeled_document)

    def test_legacy_writer_rejects_lossy_consequence_downgrade(self) -> None:
        consequence = RelationshipConsequence(
            consequence_id="consequence-1",
            relationship_id="relationship-1",
            tension_id="tension-1",
            source_turn_id="turn-1",
            source_revision="1",
            source_decision_id="decision-1",
            source_event_id="event-1",
            source_message_id="message-1",
            effects=(RelationshipConsequenceKind.HARM,),
            summary="A durable consequence.",
            recorded_at="2026-08-08T00:00:00+00:00",
        )

        with self.assertRaisesRegex(ValueError, "require format version"):
            MemoryPack(
                agent_id="agent",
                user_id="user",
                version=LEGACY_VERSION,
                relationship_consequences=[consequence],
            )

    def test_v050a1_envelope_has_fields_unknown_to_frozen_v040a8_reader(self) -> None:
        current_document = MemoryPack(agent_id="agent", user_id="user").to_dict()
        fields_unknown_to_v040a8 = set(current_document) - _root_fields("0.4.0")

        self.assertEqual(fields_unknown_to_v040a8, V050A1_EXTENSION_FIELDS)


if __name__ == "__main__":
    unittest.main()
