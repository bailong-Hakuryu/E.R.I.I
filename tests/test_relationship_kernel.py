"""Behavior tests for the v0.4 relationship-persona kernel."""

from contextlib import closing
import os
import sqlite3
import tempfile
import unittest

from erii import (
    BeliefUpdate,
    ERIIEngine,
    FileStorage,
    PersonaConflictError,
    RelationshipEventType,
    SQLiteStorage,
)


class TestExplicitArchiverLifecycle(unittest.TestCase):
    def test_engine_only_starts_background_processing_when_host_requests_it(self):
        with tempfile.TemporaryDirectory() as storage_dir:
            engine = ERIIEngine(storage_dir=storage_dir)
            try:
                self.assertFalse(engine.archiver_worker.running)

                engine.remember("agent_lumi", "user_chen", "你好", "你好呀")
                self.assertEqual(
                    engine.archiver_worker.task_queue.get_status_summary()["pending"],
                    1,
                )

                engine.start()
                first_thread = engine.archiver_worker.worker_thread
                engine.start()

                self.assertTrue(engine.archiver_worker.running)
                self.assertIs(engine.archiver_worker.worker_thread, first_thread)
            finally:
                engine.close()

    def test_host_can_process_queued_work_without_a_background_thread(self):
        with tempfile.TemporaryDirectory() as storage_dir:
            with ERIIEngine(storage_dir=storage_dir) as engine:
                engine.remember("agent_lumi", "user_chen", "记住这句话", "我会记住")

                processed = engine.process_pending(max_tasks=1)

                self.assertEqual(processed, 1)
                self.assertFalse(engine.archiver_worker.running)
                self.assertEqual(
                    engine.archiver_worker.task_queue.get_status_summary()["completed"],
                    1,
                )


class RelationshipKernelContract:
    """Shared behavior contract for each built-in storage adapter."""

    def make_storage(self, root_dir):
        raise NotImplementedError

    def test_relationships_are_isolated_but_identity_ids_are_stable(self):
        with tempfile.TemporaryDirectory() as root_dir:
            with ERIIEngine(storage_driver=self.make_storage(root_dir)) as engine:
                first = engine.initialize_relationship(
                    agent_id="agent_lumi",
                    user_id="user_chen",
                    persona_source="Lumi 重视诚实，也尊重用户边界。",
                    compiled_persona={"values": ["诚实"], "boundaries": ["不替用户做决定"]},
                )
                same = engine.initialize_relationship(
                    agent_id="agent_lumi",
                    user_id="user_chen",
                    persona_source="Lumi 重视诚实，也尊重用户边界。",
                    compiled_persona={"values": ["诚实"], "boundaries": ["不替用户做决定"]},
                )
                other_user = engine.initialize_relationship(
                    agent_id="agent_lumi",
                    user_id="user_lin",
                    persona_source="Lumi 重视诚实，也尊重用户边界。",
                )
                other_agent = engine.initialize_relationship(
                    agent_id="agent_nova",
                    user_id="user_chen",
                    persona_source="Nova 喜欢直接表达。",
                )

                self.assertEqual(first.relationship_id, same.relationship_id)
                self.assertEqual(first.persona_id, same.persona_id)
                self.assertEqual(first.agent_identity_id, other_user.agent_identity_id)
                self.assertEqual(first.user_identity_id, other_agent.user_identity_id)
                self.assertNotEqual(first.relationship_id, other_user.relationship_id)
                self.assertNotEqual(first.persona_id, other_user.persona_id)
                self.assertNotEqual(first.relationship_id, other_agent.relationship_id)

                engine.record_relationship_event(
                    "agent_lumi",
                    "user_chen",
                    RelationshipEventType.SHARED_EXPERIENCE,
                    "我们第一次一起看雪。",
                    state_delta={"familiarity": 0.08, "trust": 0.04},
                    belief_updates=[
                        BeliefUpdate(
                            key="shared.first_snow",
                            value=True,
                            confidence=1.0,
                        )
                    ],
                )

                first_snapshot = engine.get_relationship_snapshot("agent_lumi", "user_chen")
                other_snapshot = engine.get_relationship_snapshot("agent_lumi", "user_lin")

                self.assertEqual(first_snapshot.event_count, 1)
                self.assertEqual(other_snapshot.event_count, 0)
                self.assertAlmostEqual(first_snapshot.state.familiarity, 0.08)
                self.assertEqual(other_snapshot.state.familiarity, 0.0)
                self.assertIn("shared.first_snow", first_snapshot.beliefs)
                self.assertNotIn("shared.first_snow", other_snapshot.beliefs)

    def test_persona_source_is_an_immutable_authority_snapshot(self):
        with tempfile.TemporaryDirectory() as root_dir:
            with ERIIEngine(storage_driver=self.make_storage(root_dir)) as engine:
                profile = engine.initialize_relationship(
                    "agent_lumi",
                    "user_chen",
                    "原始人设文本",
                    compiled_persona={"voice": {"style": "温和"}},
                )

                with self.assertRaises(TypeError):
                    profile.blueprint.compiled["voice"]["style"] = "冷漠"

                with self.assertRaises(PersonaConflictError):
                    engine.initialize_relationship(
                        "agent_lumi",
                        "user_chen",
                        "被悄悄替换的人设文本",
                    )

                loaded = engine.get_relationship_snapshot("agent_lumi", "user_chen")
                self.assertEqual(loaded.profile.blueprint.source_text, "原始人设文本")
                self.assertEqual(loaded.profile.blueprint.compiled["voice"]["style"], "温和")

    def test_events_are_append_only_idempotent_and_rebuild_the_projection(self):
        with tempfile.TemporaryDirectory() as root_dir:
            storage = self.make_storage(root_dir)
            with ERIIEngine(storage_driver=storage) as engine:
                engine.initialize_relationship(
                    "agent_lumi",
                    "user_chen",
                    "Lumi 尊重事实。",
                )
                first = engine.record_relationship_event(
                    "agent_lumi",
                    "user_chen",
                    "observation",
                    "用户说自己喜欢伯爵茶。",
                    event_id="evt-tea",
                    state_delta={"familiarity": 0.05},
                    belief_updates=[
                        {
                            "key": "user.favorite_tea",
                            "value": "伯爵茶",
                            "confidence": 0.9,
                        }
                    ],
                )
                repeated = engine.record_relationship_event(
                    "agent_lumi",
                    "user_chen",
                    "observation",
                    "用户说自己喜欢伯爵茶。",
                    event_id="evt-tea",
                    state_delta={"familiarity": 0.05},
                    belief_updates=[
                        {
                            "key": "user.favorite_tea",
                            "value": "伯爵茶",
                            "confidence": 0.9,
                        }
                    ],
                )
                engine.record_relationship_event(
                    "agent_lumi",
                    "user_chen",
                    "correction",
                    "用户澄清现在更喜欢茉莉花茶。",
                    event_id="evt-tea-correction",
                    belief_updates=[
                        {
                            "key": "user.favorite_tea",
                            "value": "茉莉花茶",
                            "confidence": 0.95,
                        }
                    ],
                )

                self.assertEqual(first.event_id, repeated.event_id)
                snapshot = engine.get_relationship_snapshot("agent_lumi", "user_chen")
                self.assertEqual(snapshot.event_count, 2)
                self.assertAlmostEqual(snapshot.state.familiarity, 0.05)
                self.assertEqual(snapshot.beliefs["user.favorite_tea"].value, "茉莉花茶")
                self.assertEqual(
                    snapshot.beliefs["user.favorite_tea"].evidence_event_id,
                    "evt-tea-correction",
                )
                self.assertEqual(
                    snapshot.state_reasons["familiarity"].evidence_event_id,
                    "evt-tea",
                )

            with ERIIEngine(storage_driver=self.make_storage(root_dir)) as reopened:
                rebuilt = reopened.get_relationship_snapshot("agent_lumi", "user_chen")
                self.assertEqual(rebuilt.to_dict(), snapshot.to_dict())

    def test_large_or_unknown_state_changes_are_rejected_before_append(self):
        with tempfile.TemporaryDirectory() as root_dir:
            with ERIIEngine(storage_driver=self.make_storage(root_dir)) as engine:
                engine.initialize_relationship("agent_lumi", "user_chen", "初始人设")

                with self.assertRaises(ValueError):
                    engine.record_relationship_event(
                        "agent_lumi",
                        "user_chen",
                        "shared_experience",
                        "一次普通互动不应让信任瞬间拉满。",
                        state_delta={"trust": 0.8},
                    )
                with self.assertRaises(ValueError):
                    engine.record_relationship_event(
                        "agent_lumi",
                        "user_chen",
                        "shared_experience",
                        "未知维度不应进入投影。",
                        state_delta={"loyalty": 0.05},
                    )

                self.assertEqual(
                    engine.get_relationship_snapshot("agent_lumi", "user_chen").event_count,
                    0,
                )

    def test_relationship_history_round_trips_through_memory_pack(self):
        with tempfile.TemporaryDirectory() as root_dir:
            source_dir = os.path.join(root_dir, "source")
            target_dir = os.path.join(root_dir, "target")
            with ERIIEngine(storage_driver=self.make_storage(source_dir)) as source:
                profile = source.initialize_relationship(
                    "agent_lumi",
                    "user_chen",
                    "Lumi 会珍惜共同经历。",
                    compiled_persona={"values": ["珍惜共同经历"]},
                )
                source.record_relationship_event(
                    "agent_lumi",
                    "user_chen",
                    "shared_experience",
                    "我们第一次一起看雪。",
                    event_id="evt-first-snow",
                    state_delta={"intimacy": 0.06},
                )
                expected = source.get_relationship_snapshot("agent_lumi", "user_chen")
                pack = source.export_memory("agent_lumi", "user_chen")

            self.assertEqual(pack.relationship.relationship_id, profile.relationship_id)
            self.assertEqual(len(pack.relationship_events), 1)

            with ERIIEngine(storage_driver=self.make_storage(target_dir)) as target:
                target.import_memory(pack)
                imported = target.get_relationship_snapshot("agent_lumi", "user_chen")

            self.assertEqual(imported.to_dict(), expected.to_dict())


class TestFileRelationshipKernel(RelationshipKernelContract, unittest.TestCase):
    def make_storage(self, root_dir):
        return FileStorage(root_dir=root_dir)


class TestSQLiteRelationshipKernel(RelationshipKernelContract, unittest.TestCase):
    def make_storage(self, root_dir):
        return SQLiteStorage(db_path=os.path.join(root_dir, "memory.db"))

    def test_existing_database_is_migrated_without_losing_legacy_memory(self):
        with tempfile.TemporaryDirectory() as root_dir:
            db_path = os.path.join(root_dir, "legacy.db")
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute(
                    """
                    CREATE TABLE core_memories (
                        agent_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        content TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (agent_id, user_id)
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO core_memories (agent_id, user_id, content, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    ("agent_lumi", "user_chen", "旧版核心记忆", "2026-07-24 00:00:00"),
                )
                connection.commit()

            reopened = SQLiteStorage(db_path=db_path)
            with ERIIEngine(storage_driver=reopened) as engine:
                engine.initialize_relationship("agent_lumi", "user_chen", "新的关系人设快照")

                self.assertEqual(
                    reopened.get_core_memory("agent_lumi", "user_chen"),
                    "旧版核心记忆",
                )
                self.assertGreaterEqual(reopened.schema_version, 1)


class TestRelationshipPortability(unittest.TestCase):
    def test_file_pack_imports_into_sqlite_under_an_isolated_target_pair(self):
        with tempfile.TemporaryDirectory() as root_dir:
            with ERIIEngine(
                storage_driver=FileStorage(root_dir=os.path.join(root_dir, "files"))
            ) as source:
                source_profile = source.initialize_relationship(
                    "agent_lumi",
                    "user_chen",
                    "Lumi 尊重每段关系的独立历史。",
                )
                source.record_relationship_event(
                    "agent_lumi",
                    "user_chen",
                    "shared_experience",
                    "我们一起在清晨散步。",
                    event_id="evt-morning-walk",
                    state_delta={"familiarity": 0.04},
                )
                pack = source.export_memory("agent_lumi", "user_chen")

            target_storage = SQLiteStorage(db_path=os.path.join(root_dir, "target.db"))
            with ERIIEngine(storage_driver=target_storage) as target:
                target.import_memory(
                    pack,
                    agent_id="agent_lumi",
                    user_id="user_lin",
                )
                imported = target.get_relationship_snapshot("agent_lumi", "user_lin")

                self.assertNotEqual(
                    imported.profile.relationship_id,
                    source_profile.relationship_id,
                )
                self.assertEqual(imported.event_count, 1)
                self.assertAlmostEqual(imported.state.familiarity, 0.04)
                self.assertEqual(
                    imported.state_reasons["familiarity"].explanation,
                    "我们一起在清晨散步。",
                )


if __name__ == "__main__":
    unittest.main()
