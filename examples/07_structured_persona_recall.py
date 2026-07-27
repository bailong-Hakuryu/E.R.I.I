"""Explicit Persona Compilation and structured recall with an original fixture."""

from erii import ERIIEngine, RecallRequest


SOURCE = "Lumi is patient and protects other people's choices."


def main() -> None:
    with ERIIEngine(storage_dir="./example_structured_memory") as engine:
        engine.initialize_relationship("agent_lumi", "user_chen", SOURCE)
        proposal = engine.propose_persona_compilation(
            "agent_lumi",
            "user_chen",
            {
                "compiler_version": "example-v1",
                "source_spans": [
                    {
                        "span_id": "source-identity",
                        "start": 0,
                        "end": len(SOURCE),
                        "quote": SOURCE,
                    }
                ],
                "claims": [
                    {
                        "claim_id": "patient-identity",
                        "kind": "identity",
                        "statement": SOURCE,
                        "activation_tier": "foundation",
                        "basis": "explicit",
                        "source_span_ids": ["source-identity"],
                    }
                ],
            },
        )
        engine.decide_persona_compilation(
            "agent_lumi",
            "user_chen",
            proposal.proposal_id,
            proposal.revision,
            actor_id="example-owner",
            decision="approve",
        )
        result = engine.recall_structured(
            RecallRequest(
                agent_id="agent_lumi",
                user_id="user_chen",
                query="How should I answer a difficult choice?",
                audience="agent_private",
            )
        )
        print(engine.render_recall(result))


if __name__ == "__main__":
    main()
