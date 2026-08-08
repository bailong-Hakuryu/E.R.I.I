"""Public MemoryPack upgrade contracts for the v0.5 alpha lifecycle module."""

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from erii import (
    DataLifecycleCoordinator,
    LifecycleOutcome,
    LifecycleStatus,
    LifecycleTarget,
    LifecycleTargetKind,
    MemoryPack,
    RestoreRequest,
    StorageIntegrityError,
    UpgradeRequest,
)
from erii.compatibility import MEMORY_PACK_FORMAT


FIXTURE_ROOT = (
    Path(__file__).parent
    / "fixtures"
    / "lifecycle"
    / "memory-pack-v0.4.0a7"
)
FIXTURE_SOURCE = FIXTURE_ROOT / "source.erii"
PRODUCER_COMMIT = "52ec8b90082ae52462de5c00cbb582633dec9275"


class LifecycleMemoryPackUpgradeTests(unittest.TestCase):
    @staticmethod
    def _target(kind: LifecycleTargetKind, path: Path) -> LifecycleTarget:
        return LifecycleTarget(kind, str(path))

    def test_a7_fixture_has_frozen_historical_provenance(self) -> None:
        metadata = json.loads((FIXTURE_ROOT / "fixture.json").read_text("utf-8"))
        content = FIXTURE_SOURCE.read_bytes()

        self.assertEqual(metadata["fixture_contract"], "1")
        self.assertEqual(metadata["storage_kind"], "memory_pack")
        self.assertEqual(metadata["producer"]["package_version"], "0.4.0a7")
        self.assertEqual(metadata["producer"]["commit"], PRODUCER_COMMIT)
        self.assertEqual(
            metadata["producer"]["interface"],
            "erii.ERIIEngine.export_memory",
        )
        self.assertEqual(metadata["data_classification"], "synthetic_non_user_data")
        self.assertEqual(metadata["source"]["size"], len(content))
        self.assertEqual(
            metadata["source"]["sha256"],
            hashlib.sha256(content).hexdigest(),
        )

    def test_a7_pack_upgrades_side_by_side_after_verified_backup(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            source_path = root / "source-a7.erii"
            shutil.copyfile(FIXTURE_SOURCE, source_path)
            source_bytes = source_path.read_bytes()
            destination = self._target(
                LifecycleTargetKind.MEMORY_PACK,
                root / "upgraded-v050a1.erii",
            )
            backup_destination = self._target(
                LifecycleTargetKind.BACKUP,
                root / "source-a7.eriibak",
            )
            lifecycle = DataLifecycleCoordinator()
            source = lifecycle.inspect(
                self._target(LifecycleTargetKind.MEMORY_PACK, source_path)
            )

            plan = lifecycle.plan(
                UpgradeRequest(
                    source=source,
                    destination=destination,
                    backup_destination=backup_destination,
                )
            )

            self.assertEqual(plan.strategy_id, "memory-pack-0.4.0a7-to-0.5.0a2")
            self.assertEqual(source_path.read_bytes(), source_bytes)
            self.assertFalse(Path(destination.path).exists())
            self.assertFalse(Path(backup_destination.path).exists())

            report = lifecycle.execute(plan)

            self.assertEqual(report.outcome, LifecycleOutcome.APPLIED)
            self.assertEqual(source_path.read_bytes(), source_bytes)
            upgraded = lifecycle.inspect(destination)
            self.assertEqual(upgraded.status, LifecycleStatus.CURRENT)
            self.assertEqual(upgraded.detected_version, "0.5.0a2")
            upgraded_document = json.loads(Path(destination.path).read_text("utf-8"))
            self.assertEqual(
                upgraded_document["metadata"]["version"],
                "0.5.0a2",
            )
            self.assertEqual(
                upgraded_document["core_memory"],
                "角色记得和用户在雨夜交换过一张手写书签。",
            )
            upgraded_turn = upgraded_document["turn_records"][0]
            self.assertNotIn("turn_format_version", upgraded_turn)
            self.assertIn("continuity_assessment", upgraded_turn)
            parsed_turn = MemoryPack.from_json(
                Path(destination.path).read_text("utf-8")
            ).turn_records[0]
            self.assertEqual(parsed_turn.review_record.kind.value, "legacy_unavailable")
            self.assertIsNone(parsed_turn.continuity_assessment)

            backup = lifecycle.inspect(backup_destination)
            restored_target = self._target(
                LifecycleTargetKind.MEMORY_PACK,
                root / "restored-a7.erii",
            )
            lifecycle.execute(
                lifecycle.plan(
                    RestoreRequest(
                        backup=backup,
                        destination=restored_target,
                    )
                )
            )
            self.assertEqual(Path(restored_target.path).read_bytes(), source_bytes)

    def test_plan_rejects_a_parseable_pack_with_duplicate_turn_authority(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            document = json.loads(FIXTURE_SOURCE.read_text("utf-8"))
            document["turn_records"].append(dict(document["turn_records"][0]))
            source_path = root / "duplicate-turn-a7.erii"
            source_path.write_text(
                json.dumps(document, ensure_ascii=False),
                encoding="utf-8",
            )
            lifecycle = DataLifecycleCoordinator()
            source = lifecycle.inspect(
                self._target(LifecycleTargetKind.MEMORY_PACK, source_path)
            )

            with self.assertRaisesRegex(
                StorageIntegrityError,
                "semantic graph validation",
            ):
                lifecycle.plan(
                    UpgradeRequest(
                        source=source,
                        destination=self._target(
                            LifecycleTargetKind.MEMORY_PACK,
                            root / "upgraded.erii",
                        ),
                        backup_destination=self._target(
                            LifecycleTargetKind.BACKUP,
                            root / "source.eriibak",
                        ),
                    )
                )

            self.assertFalse((root / "upgraded.erii").exists())
            self.assertFalse((root / "source.eriibak").exists())

    def test_every_declared_older_pack_envelope_has_an_explicit_upgrade_route(
        self,
    ) -> None:
        older_versions = tuple(
            version
            for version in MEMORY_PACK_FORMAT.readable_versions
            if version != MEMORY_PACK_FORMAT.current_version
        )
        for version in older_versions:
            with self.subTest(version=version), tempfile.TemporaryDirectory() as root_dir:
                root = Path(root_dir)
                document = MemoryPack(
                    agent_id="fixture-agent",
                    user_id="fixture-user",
                    core_memory="synthetic portable memory",
                    version=version,
                ).to_dict()
                source_path = root / "source.erii"
                source_path.write_text(
                    json.dumps(document, ensure_ascii=False),
                    encoding="utf-8",
                )
                destination = self._target(
                    LifecycleTargetKind.MEMORY_PACK,
                    root / "upgraded.erii",
                )
                backup = self._target(
                    LifecycleTargetKind.BACKUP,
                    root / "source.eriibak",
                )
                lifecycle = DataLifecycleCoordinator()
                source = lifecycle.inspect(
                    self._target(LifecycleTargetKind.MEMORY_PACK, source_path)
                )

                plan = lifecycle.plan(UpgradeRequest(source, destination, backup))
                report = lifecycle.execute(plan)

                self.assertEqual(
                    plan.strategy_id,
                    f"memory-pack-{version}-to-{MEMORY_PACK_FORMAT.current_version}",
                )
                self.assertEqual(report.outcome, LifecycleOutcome.APPLIED)
                self.assertEqual(
                    lifecycle.inspect(destination).detected_version,
                    MEMORY_PACK_FORMAT.current_version,
                )


if __name__ == "__main__":
    unittest.main()
