"""E.R.I.I. Engine Example 05 — v0.2.0 Hybrid Retrieval & MemoryPack Migration.

Demonstrates:
1. SQLite Storage with WAL mode & (agent_id, user_id) locking.
2. BaseTaskQueue & PersistentTaskQueue host-controlled archival.
3. RRF (Reciprocal Rank Fusion) hybrid vector search with InMemoryVectorStore.
4. Exporting and importing memory snapshot via MemoryPack.

Run:
python -m examples.05_hybrid_retrieval_and_memory_pack
"""

import os
import shutil
import tempfile
from erii import (
    ERIIEngine,
    InMemoryVectorStore,
    SQLiteStorage,
)


def main():
    work_dir = tempfile.mkdtemp(prefix="erii_v020_demo_")
    print(f"=== E.R.I.I. v0.2.0 Demo Workspace: {work_dir} ===")

    try:
        # 1. Initialize Engine with SQLite Storage & InMemory Vector Store
        db_file = os.path.join(work_dir, "agent_memory.db")
        storage = SQLiteStorage(db_path=db_file)
        vector_store = InMemoryVectorStore()

        engine = ERIIEngine(
            storage_driver=storage,
            vector_store=vector_store,
        )

        agent_id = "agent_lumi"
        user_id = "player_1"

        # 2. Set Core Memory
        engine.set_core_memory(
            agent_id=agent_id,
            user_id=user_id,
            content="Lumi 是一个原创的、温柔而坦诚的 AI 陪伴角色。",
        )

        # 3. Add memories
        print("\n--- 1. Logging Memories ---")
        engine.remember(
            agent_id=agent_id,
            user_id=user_id,
            user_message="我工作时最喜欢用 VS Code 的暗黑主题。",
            bot_reply="暗黑主题对眼睛很好呢！我也很喜欢深色调。",
        )
        engine.process_pending()

        # 4. RRF Hybrid Recall
        print("\n--- 2. Executing RRF Hybrid Memory Recall ---")
        context = engine.recall(
            agent_id=agent_id,
            user_id=user_id,
            query="IDE 编辑器主题风格",
        )
        print("Recalled Prompt Context:")
        print(context)

        # 5. Export MemoryPack snapshot
        pack_file = os.path.join(work_dir, "lumi_p1_memorypack.json")
        print(f"\n--- 3. Exporting MemoryPack to {pack_file} ---")
        pack = engine.export_memory(agent_id=agent_id, user_id=user_id, export_path=pack_file)
        print(f"Exported MemoryPack version: {pack.version}, Nodes count: {len(pack.nodes)}")

        # 6. Import into a new Engine instance
        print("\n--- 4. Importing MemoryPack into a fresh Engine instance ---")
        new_storage_dir = os.path.join(work_dir, "migrated_file_memory")
        fresh_engine = ERIIEngine(storage_dir=new_storage_dir)
        fresh_engine.import_memory(pack_file)

        migrated_context = fresh_engine.recall(
            agent_id=agent_id,
            user_id=user_id,
            query="IDE 主题",
        )
        print("Migrated Engine Recalled Context:")
        print(migrated_context)

        engine.close()
        fresh_engine.close()
        print("\n=== v0.2.0 Demo Completed Successfully! ===")

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
