"""E.R.I.I. v0.4.0a1 relationship-persona kernel example."""

from erii import BeliefUpdate, ERIIEngine, SQLiteStorage


def main():
    storage = SQLiteStorage(db_path="./example_relationship.db")
    with ERIIEngine(storage_driver=storage) as engine:
        profile = engine.initialize_relationship(
            agent_id="agent_lumi",
            user_id="user_chen",
            persona_source="Lumi 重视诚实，也尊重用户边界。",
            compiled_persona={
                "values": ["诚实"],
                "boundaries": ["不替用户做决定"],
            },
        )

        engine.record_relationship_event(
            agent_id="agent_lumi",
            user_id="user_chen",
            event_type="shared_experience",
            content="我们第一次一起看雪。",
            event_id="example-first-snow",
            state_delta={"familiarity": 0.08, "trust": 0.04},
            belief_updates=[
                BeliefUpdate(
                    key="shared.first_snow",
                    value=True,
                    confidence=1.0,
                )
            ],
        )

        snapshot = engine.get_relationship_snapshot("agent_lumi", "user_chen")
        print(f"relationship_id: {profile.relationship_id}")
        print(f"persona_id: {profile.persona_id}")
        print(f"trust: {snapshot.state.trust:.2f}")
        print(f"reason: {snapshot.state_reasons['trust'].explanation}")


if __name__ == "__main__":
    main()
