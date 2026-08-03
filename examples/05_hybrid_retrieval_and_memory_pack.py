"""E.R.I.I. Engine Example 05 — Hybrid Retrieval & MemoryPack Round Trip.

Demonstrates:
1. SQLite Storage with WAL mode & (agent_id, user_id) locking.
2. Canonical Source Turn recording and synchronous reliable archival.
3. RRF (Reciprocal Rank Fusion) hybrid vector search with InMemoryVectorStore.
4. Exporting and importing memory snapshot via MemoryPack.

Run:
python -m examples.05_hybrid_retrieval_and_memory_pack
"""

import os
import shutil
import tempfile
from erii import (
    ERIIConfig,
    ERIIEngine,
    InMemoryVectorStore,
    SQLiteStorage,
    __version__,
)


if __package__:
    from ._shared import (
        DeterministicMemoryExtractor,
        record_and_archive_visible_exchange,
    )
else:
    from _shared import (  # type: ignore[import-not-found]
        DeterministicMemoryExtractor,
        record_and_archive_visible_exchange,
    )


def main():
    work_dir = tempfile.mkdtemp(prefix="erii_demo_")
    print(f"=== E.R.I.I. v{__version__} Demo Workspace: {work_dir} ===")

    try:
        # 1. Initialize Engine with SQLite Storage & InMemory Vector Store
        db_file = os.path.join(work_dir, "agent_memory.db")
        storage = SQLiteStorage(db_path=db_file)
        vector_store = InMemoryVectorStore()
        config = ERIIConfig(
            storage_dir=os.path.join(work_dir, "runtime"),
            async_archival=False,
        )
        extractor = DeterministicMemoryExtractor(
            timeline_content="用户告诉 Lumi 自己工作时喜欢 VS Code 暗黑主题。",
            memory_content="用户工作时偏好 VS Code 的暗黑主题。",
            tags=("vscode", "暗黑主题", "编辑器"),
        )

        agent_id = "agent_lumi"
        user_id = "player_1"

        with ERIIEngine(
            storage_driver=storage,
            vector_store=vector_store,
            config=config,
            memory_extractor=extractor,
        ) as engine:
            # 2. Establish persona authority and relationship-local core context.
            persona_source = "Lumi 是一个原创的、温柔而坦诚的 AI 陪伴角色。"
            engine.initialize_relationship(agent_id, user_id, persona_source)
            engine.set_core_memory(
                agent_id=agent_id,
                user_id=user_id,
                content=persona_source,
            )

            # 3. Record and synchronously archive one canonical Source Turn.
            print("\n--- 1. Recording and Archiving Memory ---")
            record_and_archive_visible_exchange(
                engine,
                agent_id=agent_id,
                user_id=user_id,
                user_message="我工作时最喜欢用 VS Code 的暗黑主题。",
                agent_message="暗黑主题对眼睛很好呢！我也很喜欢深色调。",
                turn_id="lumi-vscode-theme-turn",
                actor_id="examples.hybrid-memory-pack-host",
            )

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
            pack = engine.export_memory(
                agent_id=agent_id,
                user_id=user_id,
                export_path=pack_file,
            )
            print(
                f"Exported MemoryPack version: {pack.version}, "
                f"Nodes count: {len(pack.nodes)}"
            )

        # 6. Import into a new Engine instance
        print("\n--- 4. Importing MemoryPack into a fresh Engine instance ---")
        new_storage_dir = os.path.join(work_dir, "migrated_file_memory")
        with ERIIEngine(storage_dir=new_storage_dir) as fresh_engine:
            fresh_engine.import_memory(pack_file)
            migrated_context = fresh_engine.recall(
                agent_id=agent_id,
                user_id=user_id,
                query="IDE 主题",
            )
        print("Migrated Engine Recalled Context:")
        print(migrated_context)

        print(f"\n=== v{__version__} Demo Completed Successfully! ===")

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
