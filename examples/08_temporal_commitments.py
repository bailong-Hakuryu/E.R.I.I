"""Record, recall, and resolve append-only temporal relationship events."""

from tempfile import TemporaryDirectory

from erii import (
    ERIIEngine,
    OpenLoopResolutionKind,
    PersonaDelivery,
    PromiseResolutionKind,
    PromiseResponsibleParty,
    RecallAudience,
    RecallOptions,
    RecallRequest,
    RecallTemporalContext,
    WorldMoment,
    WorldTime,
)


def main() -> None:
    with TemporaryDirectory(prefix="erii-temporal-") as storage_dir:
        with ERIIEngine(storage_dir=storage_dir) as engine:
            engine.initialize_relationship(
                "agent_lumi",
                "user_chen",
                "Lumi is patient and treats commitments seriously.",
            )

            promise = engine.record_promise(
                "agent_lumi",
                "user_chen",
                "bring the revised travel plan",
                (PromiseResponsibleParty.AGENT,),
                due_at=WorldMoment(
                    clock_id="story-day",
                    display_value="day 3",
                    order_value=3,
                ),
            )
            open_loop = engine.record_open_loop(
                "agent_lumi",
                "user_chen",
                "Choose the destination together",
                expected_continuation="Ask which city feels right.",
            )

            request = RecallRequest(
                agent_id="agent_lumi",
                user_id="user_chen",
                query="What should Lumi remember now?",
                audience=RecallAudience.AGENT_PRIVATE,
                options=RecallOptions(persona_delivery=PersonaDelivery.FULL),
                temporal_context=RecallTemporalContext(
                    world_time=WorldTime(
                        clock_id="story-day",
                        display_value="day 4",
                        order_value=4,
                    )
                ),
            )
            before_resolution = engine.recall_structured(request)
            print("Current signals:")
            for signal in before_resolution.signals:
                print(f"- {signal.signal_type.value}: {signal.summary}")

            engine.resolve_promise(
                "agent_lumi",
                "user_chen",
                promise.event_id,
                PromiseResolutionKind.FULFILLED,
            )
            engine.resolve_open_loop(
                "agent_lumi",
                "user_chen",
                open_loop.event_id,
                OpenLoopResolutionKind.COMPLETED,
            )

            after_resolution = engine.recall_structured(request)
            print(f"Signals after resolution: {len(after_resolution.signals)}")


if __name__ == "__main__":
    main()
