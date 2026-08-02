"""Generate the synthetic MemoryPack a7 lifecycle fixture with historical code.

Run this script with ``PYTHONPATH`` pointing at commit
``52ec8b90082ae52462de5c00cbb582633dec9275``.  It deliberately uses only that
checkout's public E.R.I.I. interfaces so the frozen artifact is not reconstructed
by the current reader.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile

import erii
from erii import ERIIEngine, FileStorage


PRODUCER_COMMIT = "52ec8b90082ae52462de5c00cbb582633dec9275"
EXPECTED_VERSION = "0.4.0a7"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if erii.__version__ != EXPECTED_VERSION:
        raise RuntimeError(
            f"historical fixture requires E.R.I.I. {EXPECTED_VERSION}, "
            f"got {erii.__version__}"
        )

    args.output.mkdir(parents=True, exist_ok=True)
    source_path = args.output / "source.erii"
    with tempfile.TemporaryDirectory() as root_dir:
        storage = FileStorage(str(Path(root_dir) / "historical-store"))
        with ERIIEngine(storage_driver=storage) as engine:
            engine.initialize_relationship(
                "fixture_agent",
                "fixture_user",
                "An original synthetic character who values patience and honest memory.",
            )
            storage.save_core_memory(
                "fixture_agent",
                "fixture_user",
                "角色记得和用户在雨夜交换过一张手写书签。",
            )
            storage.add_timeline_entry(
                "fixture_agent",
                "fixture_user",
                "我们在雨声里读完了同一篇故事。 ☔",
                timestamp="2026-07-29 22:10:00+08:00",
            )
            engine.record_turn(
                "fixture_agent",
                "fixture_user",
                "这场雨会停吗？",
                "会停，但书签会替我们记得这一页。",
                turn_id="fixture-turn-rain-a7",
                processing_channels=(),
            )
            engine.record_relationship_event(
                "fixture_agent",
                "fixture_user",
                "shared_experience",
                "两个人在雨夜交换了手写书签。",
                event_id="fixture-event-bookmark-a7",
                occurred_at="2026-07-29T22:12:00+08:00",
                state_delta={"familiarity": 0.03},
            )
            source_path.write_text(
                engine.export_memory("fixture_agent", "fixture_user").to_json(),
                encoding="utf-8",
            )

    content = source_path.read_bytes()
    metadata = {
        "fixture_contract": "1",
        "storage_kind": "memory_pack",
        "producer": {
            "package_version": EXPECTED_VERSION,
            "commit": PRODUCER_COMMIT,
            "interface": "erii.ERIIEngine.export_memory",
        },
        "data_classification": "synthetic_non_user_data",
        "source": {
            "path": source_path.name,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
        },
        "expected_inspection": {
            "format_id": "erii.memory-pack",
            "detected_version": EXPECTED_VERSION,
            "target_version": "0.4.0a8",
            "file_count": 1,
        },
    }
    (args.output / "fixture.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
