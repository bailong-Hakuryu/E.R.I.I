"""
Complete Turn Lifecycle Integration Example

This example demonstrates the canonical integration path for a chat host
that wants to use E.R.I.I. for character memory and continuity.
"""

from erii import (
    ERIIEngine,
    ERIIConfig,
    
    RecallRequest,
    
    TurnStatus,
    DeliveryDisposition,
    SourceProcessingChannel,
)
import uuid


def example_1_basic_two_phase_turn():
    """Example 1: Basic two-phase turn recording (recommended)."""
    print("=" * 60)
    print("Example 1: Two-Phase Turn Recording")
    print("=" * 60)

    # Initialize engine
    engine = ERIIEngine(storage_dir="./temp_demo")

    # Initialize relationship
    engine.initialize_relationship(
        "agent_lumi",
        "user_chen",
        persona_source="A thoughtful AI assistant who values meaningful conversation.",
        source_format="text/markdown",
        source_name="lumi_persona.md",
    )

    # Phase 1: Begin turn (capture user message)
    print("\n1. User sends message...")
    _turn = engine.begin_turn(
        "agent_lumi",
        "user_chen",
        "今天天气真好！我们可以出去散步吗？",
        turn_id=f"turn-{uuid.uuid4()}",
    )
    print(f"   ✓ Turn opened: {turn.turn_id}")
    print(f"   Status: {turn.status}")

    # Phase 2: Recall prior context (before reply exists)
    print("\n2. Recall prior context...")
    context = engine.recall_structured(
        RecallRequest(
            agent_id="agent_lumi",
            user_id="user_chen",
            query="今天天气真好！我们可以出去散步吗？",
        )
    )
    print(f"   ✓ Context retrieved ({len(context.memory_blocks)} blocks)")

    # Phase 3: Generate reply (your LLM here)
    print("\n3. Generate reply...")
    agent_reply = "是啊！天气这么好，散步是个不错的主意。我们可以去公园。"
    print(f"   ✓ Reply generated")

    # Phase 4: Complete turn (seal the reply)
    print("\n4. Complete turn...")
    _receipt = engine.complete_turn(
        "agent_lumi",
        "user_chen",
        turn.turn_id,
        agent_reply,
        delivery_disposition=DeliveryDisposition.SHOWN,
        processing_channels=[
            SourceProcessingChannel.MEMORY_EXTRACTION,
            SourceProcessingChannel.RELATIONSHIP_EVENT_EXTRACTION,
        ],
    )
    print(f"   ✓ Turn completed: {receipt.turn_id}")
    print(f"   User fingerprint: {receipt.user_message_fingerprint[:8]}...")
    print(f"   Agent fingerprint: {receipt.agent_message_fingerprint[:8]}...")

    # Phase 5: Archive (extract memories)
    print("\n5. Archive turn...")
    submission = engine.archive_turn("agent_lumi", "user_chen", turn.turn_id)
    print(f"   ✓ Archival submitted: {submission.task_id}")

    engine.close()
    print("\n✓ Example 1 completed!\n")


def example_2_one_shot_turn():
    """Example 2: One-shot turn recording (for historical data)."""
    print("=" * 60)
    print("Example 2: One-Shot Turn Recording")
    print("=" * 60)

    engine = ERIIEngine(storage_dir="./temp_demo")

    # Ensure relationship exists
    try:
        engine.initialize_relationship(
            "agent_lumi",
            "user_chen",
            persona_source="...",
            source_format="text/markdown",
        )
    except Exception:
        pass  # Already exists

    # Record historical conversation (both messages already shown)
    print("\n1. Recording historical turn...")
    receipt = engine.record_turn(
        "agent_lumi",
        "user_chen",
        user_message="你叫什么名字？",
        agent_message="我是 Lumi，很高兴认识你！",
        turn_id=f"historical-{uuid.uuid4()}",
        delivery_disposition=DeliveryDisposition.SHOWN,
    )

    print(f"   ✓ Turn recorded: {receipt.turn_id}")
    print(f"   Status: {receipt.status}")

    engine.close()
    print("\n✓ Example 2 completed!\n")


def example_3_error_handling():
    """Example 3: Error handling and retry logic."""
    print("=" * 60)
    print("Example 3: Error Handling")
    print("=" * 60)

    from erii import TurnConflictError, TurnTerminalConflictError

    engine = ERIIEngine(storage_dir="./temp_demo")

    try:
        engine.initialize_relationship("agent_lumi", "user_chen", "...", "text/markdown")
    except Exception:
        pass

    turn_id = f"turn-{uuid.uuid4()}"
    user_msg = "测试消息"

    # Open turn
    print("\n1. Open turn...")
    _turn = engine.begin_turn("agent_lumi", "user_chen", user_msg, turn_id=turn_id)
    print(f"   ✓ Turn opened: {turn_id}")

    # Simulate retry with same content (should be idempotent)
    print("\n2. Retry with same content (idempotent)...")
    try:
        _turn2 = engine.begin_turn("agent_lumi", "user_chen", user_msg, turn_id=turn_id)
        print(f"   ✓ Retry succeeded (same content)")
    except TurnConflictError as e:
        print(f"   ⚠️  Conflict (expected if different content): {e}")

    # Try to complete
    print("\n3. Complete turn...")
    _receipt = engine.complete_turn(
        "agent_lumi", "user_chen", turn_id, "测试回复", delivery_disposition=DeliveryDisposition.SHOWN
    )
    print(f"   ✓ Completed")

    # Try to complete again (should fail - terminal state)
    print("\n4. Try to complete again (should fail)...")
    try:
        engine.complete_turn(
            "agent_lumi", "user_chen", turn_id, "另一个回复", delivery_disposition=DeliveryDisposition.SHOWN
        )
        print(f"   ✗ Should have failed!")
    except TurnTerminalConflictError as e:
        print(f"   ✓ Correctly rejected: Turn already in terminal state")

    engine.close()
    print("\n✓ Example 3 completed!\n")


def example_4_turn_abandonment():
    """Example 4: Abandoning a turn when generation fails."""
    print("=" * 60)
    print("Example 4: Turn Abandonment")
    print("=" * 60)

    engine = ERIIEngine(storage_dir="./temp_demo")

    try:
        engine.initialize_relationship("agent_lumi", "user_chen", "...", "text/markdown")
    except Exception:
        pass

    turn_id = f"turn-{uuid.uuid4()}"

    # Open turn
    print("\n1. Open turn...")
    _turn = engine.begin_turn(
        "agent_lumi", "user_chen", "生成一个很难的回复", turn_id=turn_id
    )
    print(f"   ✓ Turn opened: {turn_id}")

    # Simulate generation failure
    print("\n2. Try to generate reply...")
    try:
        # Simulate failure
        raise RuntimeError("LLM API timeout")
    except RuntimeError as e:
        print(f"   ✗ Generation failed: {e}")

        # Abandon the turn
        print("\n3. Abandon turn...")
        receipt = engine.abandon_turn("agent_lumi", "user_chen", turn_id)
        print(f"   ✓ Turn abandoned: {receipt.turn_id}")
        print(f"   Status: {receipt.status}")
        print(f"   Note: User message is kept, but no agent reply recorded")

    engine.close()
    print("\n✓ Example 4 completed!\n")


def example_5_listing_and_querying():
    """Example 5: Listing and querying turns."""
    print("=" * 60)
    print("Example 5: Listing and Querying Turns")
    print("=" * 60)

    engine = ERIIEngine(storage_dir="./temp_demo")

    try:
        engine.initialize_relationship("agent_lumi", "user_chen", "...", "text/markdown")
    except Exception:
        pass

    # Record a few turns
    print("\n1. Recording multiple turns...")
    for i in range(3):
        engine.record_turn(
            "agent_lumi",
            "user_chen",
            f"用户消息 {i+1}",
            f"AI 回复 {i+1}",
            turn_id=f"turn-demo-{i+1}",
            delivery_disposition=DeliveryDisposition.SHOWN,
        )
    print(f"   ✓ Recorded 3 turns")

    # List all turns
    print("\n2. List all turns...")
    all_turns = engine.list_turns("agent_lumi", "user_chen")
    print(f"   Total turns: {len(all_turns)}")

    # List by status
    print("\n3. List completed turns...")
    completed = engine.list_turns("agent_lumi", "user_chen", status=TurnStatus.COMPLETED)
    print(f"   Completed turns: {len(completed)}")

    # Get specific turn
    print("\n4. Get specific turn...")
    turn = engine.get_turn("agent_lumi", "user_chen", "turn-demo-1")
    print(f"   Turn ID: {turn.turn_id}")
    print(f"   User: {turn.transcript.user_message.content}")
    print(f"   Agent: {turn.transcript.agent_message.content}")

    engine.close()
    print("\n✓ Example 5 completed!\n")


def example_6_full_integration():
    """Example 6: Complete integration with recall."""
    print("=" * 60)
    print("Example 6: Full Integration Flow")
    print("=" * 60)

    engine = ERIIEngine(
        storage_dir="./temp_demo",
        config=ERIIConfig(async_archival=False),  # Synchronous for demo
    )

    # Setup
    print("\n1. Initialize relationship...")
    try:
        engine.initialize_relationship(
            "agent_lumi",
            "user_chen",
            persona_source="Lumi is a thoughtful AI who remembers past conversations.",
            source_format="text/markdown",
            source_name="lumi.md",
        )
        print("   ✓ Relationship initialized")
    except:
        print("   ✓ Relationship already exists")

    # First conversation
    print("\n2. First conversation...")
    _turn1 = engine.begin_turn(
        "agent_lumi", "user_chen", "我最喜欢的颜色是蓝色", turn_id="turn-color"
    )
    _receipt1 = engine.complete_turn(
        "agent_lumi",
        "user_chen",
        "turn-color",
        "好的，我记住了！你喜欢蓝色。",
        delivery_disposition=DeliveryDisposition.SHOWN,
        processing_channels=[SourceProcessingChannel.MEMORY_EXTRACTION],
    )
    engine.archive_turn("agent_lumi", "user_chen", "turn-color")
    print("   ✓ First turn completed and archived")

    # Second conversation (should recall previous)
    print("\n3. Second conversation (with recall)...")
    _turn2 = engine.begin_turn(
        "agent_lumi", "user_chen", "我喜欢什么颜色？", turn_id="turn-recall-color"
    )

    # Recall should include the color preference
    context = engine.recall_structured(
        RecallRequest(agent_id="agent_lumi", user_id="user_chen", query="我喜欢什么颜色？")
    )

    print(f"   ✓ Recalled {len(context.memory_blocks)} memory blocks")
    if context.memory_blocks:
        print(f"   Sample memory: {context.memory_blocks[0].content[:50]}...")

    # Generate reply using context (simplified)
    reply = "你喜欢蓝色！我记得你之前告诉过我。"

    _receipt2 = engine.complete_turn(
        "agent_lumi",
        "user_chen",
        "turn-recall-color",
        reply,
        delivery_disposition=DeliveryDisposition.SHOWN,
    )
    print(f"   ✓ Second turn completed with recall")

    engine.close()
    print("\n✓ Example 6 completed!\n")


def cleanup():
    """Clean up demo data."""
    import shutil
    import os

    if os.path.exists("./temp_demo"):
        shutil.rmtree("./temp_demo")
        print("✓ Cleaned up demo data")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("E.R.I.I. Turn Lifecycle Integration Examples")
    print("=" * 60 + "\n")

    try:
        example_1_basic_two_phase_turn()
        example_2_one_shot_turn()
        example_3_error_handling()
        example_4_turn_abandonment()
        example_5_listing_and_querying()
        example_6_full_integration()

        print("=" * 60)
        print("All examples completed successfully!")
        print("=" * 60)

    finally:
        cleanup()
