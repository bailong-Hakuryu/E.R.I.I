"""Backward-compatibility contract for durable Lifecycle Plan v1 documents."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from erii import FileStorage
from erii._lifecycle.plan_codec import (
    canonical_json as codec_canonical_json,
    decode_plan,
    decode_strict_json,
    encode_plan,
)
from erii.data_lifecycle import (
    BackupRequest,
    DataLifecycleCoordinator,
    LifecycleAssessment,
    LifecycleContentIdentity,
    LifecycleOutcome,
    LifecyclePlan,
    LifecycleTarget,
    LifecycleTargetKind,
)
from erii.errors import LifecyclePlanError


_V1_PLAN_FIELDS = {
    "contract_version",
    "operation",
    "operation_id",
    "source",
    "destination",
    "destination_parent",
    "content",
    "plan_digest",
}


def _canonical_json(value: object) -> bytes:
    """Encodes the historical v1 canonical JSON representation."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _target_document(target: LifecycleTarget) -> dict[str, object]:
    return {"kind": target.kind.value, "path": target.path}


def _assessment_document(assessment: LifecycleAssessment) -> dict[str, object]:
    return {
        "target": _target_document(assessment.target),
        "status": assessment.status.value,
        "format_id": assessment.format_id,
        "detected_version": assessment.detected_version,
        "current_version": assessment.current_version,
        "fingerprint": assessment.fingerprint,
        "file_count": assessment.file_count,
        "warnings": list(assessment.warnings),
    }


def _content_document(content: LifecycleContentIdentity) -> dict[str, object]:
    return {
        "kind": content.kind.value,
        "status": content.status.value,
        "format_id": content.format_id,
        "detected_version": content.detected_version,
        "current_version": content.current_version,
        "fingerprint": content.fingerprint,
        "file_count": content.file_count,
    }


def _destination_parent_document(destination: LifecycleTarget) -> dict[str, object]:
    parent = Path(destination.path).parent
    info = os.stat(parent, follow_symlinks=False)
    resolved = os.path.normcase(os.path.normpath(str(parent.resolve(strict=True))))
    return {
        "resolved_path": resolved,
        # Lifecycle Plan v1 stored filesystem identities as decimal strings so
        # large Windows values survived IEEE-754 JSON tooling unchanged.
        "device": str(info.st_dev),
        "inode": str(info.st_ino),
    }


def _v1_plan_document(
    request: BackupRequest,
    destination_assessment: LifecycleAssessment,
    *,
    operation: str = "backup",
) -> dict[str, object]:
    """Builds a strict external v1 document without production codec helpers."""
    intent = {
        "contract_version": "1",
        "operation": operation,
        "source": _assessment_document(request.source),
        "destination": _assessment_document(destination_assessment),
        "destination_parent": _destination_parent_document(request.destination),
        "content": _content_document(
            LifecycleContentIdentity.from_assessment(request.source)
        ),
    }
    operation_id = _sha256_json(intent)
    body = {**intent, "operation_id": operation_id}
    return {**body, "plan_digest": _sha256_json(body)}


class LifecyclePlanV1CompatibilityTests(unittest.TestCase):
    @staticmethod
    def _backup_request(root: Path) -> tuple[DataLifecycleCoordinator, BackupRequest]:
        source_path = root / "legacy-file-storage"
        FileStorage(str(source_path)).save_core_memory(
            "agent_lumi",
            "user_chen",
            "A stable historical persona source.",
        )
        lifecycle = DataLifecycleCoordinator()
        source = lifecycle.inspect(
            LifecycleTarget(LifecycleTargetKind.FILE_STORAGE, str(source_path))
        )
        request = BackupRequest(
            source=source,
            destination=LifecycleTarget(
                LifecycleTargetKind.BACKUP,
                str(root / "snapshot.eriibak"),
            ),
        )
        return lifecycle, request

    def test_strict_v1_backup_plan_round_trips_and_executes(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            lifecycle, request = self._backup_request(Path(root_dir))
            destination = lifecycle.inspect(request.destination)
            document = _v1_plan_document(request, destination)

            plan = LifecyclePlan.from_json(_canonical_json(document).decode("utf-8"))

            round_trip = json.loads(plan.to_json())
            self.assertEqual(set(round_trip), _V1_PLAN_FIELDS)
            self.assertEqual(round_trip, document)
            self.assertEqual(round_trip["contract_version"], "1")
            self.assertEqual(LifecyclePlan.from_json(plan.to_json()), plan)

            report = lifecycle.execute(plan)

            self.assertEqual(report.outcome, LifecycleOutcome.APPLIED)
            self.assertEqual(report.operation_id, document["operation_id"])
            self.assertEqual(report.plan_digest, document["plan_digest"])
            self.assertTrue(Path(request.destination.path).is_dir())

    def test_internal_codec_owns_the_strict_v1_reader_and_writer(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            lifecycle, request = self._backup_request(Path(root_dir))
            destination = lifecycle.inspect(request.destination)
            json_text = _canonical_json(
                _v1_plan_document(request, destination)
            ).decode("utf-8")

            plan = decode_plan(json_text)

            self.assertIs(type(plan), LifecyclePlan)
            self.assertEqual(encode_plan(plan), json_text)

    def test_internal_codec_rejects_nonstandard_json_numbers(self) -> None:
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant):
                with self.assertRaises(ValueError):
                    decode_strict_json(
                        f'{{"value":{constant}}}',
                        label="lifecycle plan",
                    )

        with self.assertRaises(ValueError):
            codec_canonical_json({"value": float("nan")})

    def test_v1_document_cannot_claim_the_v2_upgrade_operation(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            lifecycle, request = self._backup_request(Path(root_dir))
            destination = lifecycle.inspect(request.destination)
            document = _v1_plan_document(
                request,
                destination,
                operation="upgrade",
            )

            with self.assertRaises(LifecyclePlanError):
                LifecyclePlan.from_json(_canonical_json(document).decode("utf-8"))

    def test_v2_document_rejects_unknown_top_level_fields(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            lifecycle, request = self._backup_request(Path(root_dir))
            current_plan = lifecycle.plan(request)
            document = json.loads(current_plan.to_json())
            self.assertEqual(document["contract_version"], "3")
            document["contract_version"] = "2"
            document.pop("selector")
            intent = {
                key: value
                for key, value in document.items()
                if key not in {"operation_id", "plan_digest"}
            }
            document["operation_id"] = _sha256_json(intent)
            document["plan_digest"] = _sha256_json(
                {**intent, "operation_id": document["operation_id"]}
            )
            v2_plan = LifecyclePlan.from_json(
                _canonical_json(document).decode("utf-8")
            )
            self.assertEqual(v2_plan.to_json(), _canonical_json(document).decode("utf-8"))
            self.assertEqual(lifecycle.execute(v2_plan).outcome, LifecycleOutcome.APPLIED)
            document["future_unverified_action"] = {"enabled": True}

            with self.assertRaises(LifecyclePlanError):
                LifecyclePlan.from_json(json.dumps(document))

    def test_v3_document_rejects_unknown_selector_fields(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            lifecycle, request = self._backup_request(Path(root_dir))
            document = json.loads(lifecycle.plan(request).to_json())
            self.assertEqual(document["contract_version"], "3")
            self.assertIsNone(document["selector"])
            document["selector"] = {"future_unverified_action": True}

            with self.assertRaises(LifecyclePlanError):
                LifecyclePlan.from_json(json.dumps(document))


if __name__ == "__main__":
    unittest.main()
