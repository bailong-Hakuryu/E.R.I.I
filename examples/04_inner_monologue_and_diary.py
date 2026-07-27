"""E.R.I.I. Example 04: Inner Monologue, Diary Timeline & Narrative Tension.

Demonstrates:
1. Recording timestamped public diary entries and internal monologue thoughts.
2. Querying character diary timelines for UI rendering.
3. Unresolved narrative suspense hold-back (Zeigarnik effect).
4. Resolving suspense nodes when story events complete.
"""

import argparse
import shutil
import tempfile
from erii import ERIIEngine


def main(mode: str = "AB"):
    mode = mode.upper()
    if mode not in ("A", "B", "AB", "BOTH"):
        mode = "AB"

    tmp_dir = tempfile.mkdtemp()
    try:
        # Initialize E.R.I.I. Engine
        engine = ERIIEngine(storage_dir=tmp_dir)

        agent_id = "agent_lumi"
        user_id = "player_1"

        print(f"=== 1. Record Character Inner Monologue & Diary Entries (Selected Mode: {mode}) ===")

        # Base Entry: Public diary entry with timestamp & unresolved suspense (Zeigarnik effect)
        engine.remember_thought(
            agent_id=agent_id,
            user_id=user_id,
            content="Lumi 要带我去公园，我很开心。",
            visibility="public_log",
            is_unresolved=True,
            emotional_score=0.9,
            foreshadowing_tags=["park_visit", "anticipation"],
            created_at="2026-07-24 09:30:00",
        )

        # Style A: Warm Affection / Heartfelt Monologue
        if "A" in mode or mode == "BOTH":
            engine.remember_thought(
                agent_id=agent_id,
                user_id=user_id,
                content="（心里悄悄想：无论未来如何，此刻能和你一起走在阳光下，就是我最珍视的宝物…）",
                visibility="internal_monologue",
                is_unresolved=True,
                emotional_score=0.95,
                foreshadowing_tags=["heartfelt_cherish", "deep_affection"],
                created_at="2026-07-24 09:31:00",
            )

        # Style B: Dramatic Mystery / Secret Promise Monologue
        if "B" in mode or mode == "BOTH":
            engine.remember_thought(
                agent_id=agent_id,
                user_id=user_id,
                content="（心里悄悄想：他今天送我的那个八音盒里，到底藏着什么不为人知的约定呢…）",
                visibility="internal_monologue",
                is_unresolved=True,
                emotional_score=0.85,
                foreshadowing_tags=["mystery_box", "promise"],
                created_at="2026-07-24 09:32:00",
            )

        # Resolved public diary entry
        engine.remember_thought(
            agent_id=agent_id,
            user_id=user_id,
            content="在公园收到了印着风筝的大脸棉花糖，太浪漫了！",
            visibility="public_log",
            is_unresolved=False,
            emotional_score=0.8,
            created_at="2026-07-24 15:00:00",
        )

        print("\n=== 2. Fetch Public Diary Timeline for User UI ===")
        diary_timeline = engine.get_diary_timeline(agent_id=agent_id, user_id=user_id)

        for entry in diary_timeline:
            status = "【未完待续/悬念】" if entry.get("is_unresolved") else "【已实现】"
            print(f"[{entry['created_at']}] {status} {entry['content']} (权重: {entry['effective_weight']})")

        print("\n=== 3. Query Internal Monologue (Agent Inner Recall Only) ===")
        internal_thoughts = engine.get_inner_monologue(
            agent_id=agent_id,
            user_id=user_id,
            visibility="internal_monologue",
        )
        for t in internal_thoughts:
            print(f"[{t['created_at']}] [内部独白] {t['content']}")

        print("\n=== 4. Resolve Suspense Node (Story Progression) ===")
        unresolved_nodes = engine.get_inner_monologue(
            agent_id=agent_id,
            user_id=user_id,
            unresolved_only=True,
        )
        if unresolved_nodes:
            target_id = unresolved_nodes[0]["node_id"]
            print(f"Resolving suspense node ID: {target_id}")
            engine.resolve_thought(agent_id, user_id, target_id)

        print("\n=== 5. Updated Public Diary Timeline After Resolution ===")
        updated_timeline = engine.get_diary_timeline(agent_id=agent_id, user_id=user_id)
        for entry in updated_timeline:
            status = "【未完待续/悬念】" if entry.get("is_unresolved") else "【已完结】"
            print(f"[{entry['created_at']}] {status} {entry['content']}")

        engine.close()
        print("\nDemo completed successfully!")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="E.R.I.I. Inner Monologue & Diary Example")
    parser.add_argument(
        "--mode",
        choices=["A", "B", "AB"],
        default="AB",
        help="Select monologue style: A (Warm Affection), B (Dramatic Mystery), AB (Both)",
    )
    args = parser.parse_args()
    main(mode=args.mode)
