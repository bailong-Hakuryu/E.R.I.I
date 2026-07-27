"""Deterministic Markdown renderer for structured recall results."""

from __future__ import annotations

from typing import Callable, List, Optional

from erii.models.recall import (
    EventRecallProjection,
    MemoryRecallProjection,
    PersonaRecallProjection,
    RecallAudience,
    RecallResult,
    RelationshipNarrativeProjection,
)
from erii.renderers.base import RecallRenderBudgetError, require_matching_audience


TextCostEstimator = Callable[[str], int]


def _default_cost_estimator(text: str) -> int:
    """Returns a stable conservative text-unit cost without model dependencies."""

    return len(text)


class MarkdownRecallRenderer:
    """Formats an already selected result without storage, LLM, or filtering."""

    def __init__(
        self,
        *,
        audience: RecallAudience,
        max_output_cost: Optional[int] = None,
        cost_estimator: Optional[TextCostEstimator] = None,
    ) -> None:
        if max_output_cost is not None and max_output_cost < 1:
            raise ValueError("max_output_cost must be positive")
        self.audience = RecallAudience(audience)
        self.max_output_cost = max_output_cost
        self._cost_estimator = cost_estimator or _default_cost_estimator

    @staticmethod
    def _persona_line(item: PersonaRecallProjection, index: int) -> str:
        return f"{index}. [{item.kind}] {item.content}"

    @staticmethod
    def _relationship_line(
        item: RelationshipNarrativeProjection,
        index: int,
    ) -> str:
        return f"{index}. [{item.kind}] {item.content}"

    @staticmethod
    def _memory_line(item: MemoryRecallProjection, index: int) -> str:
        time_prefix = f"[{item.created_at}] " if item.created_at else ""
        return f"{index}. {time_prefix}[{item.memory_type.upper()}] {item.content}"

    @staticmethod
    def _event_line(item: EventRecallProjection, index: int) -> str:
        times = [f"recorded: {item.recorded_at}"]
        if item.occurred_at is not None:
            times.insert(0, f"occurred: {item.occurred_at}")
        return f"{index}. [{item.event_type}] {item.summary} ({'; '.join(times)})"

    def render(self, result: RecallResult) -> str:
        """Renders a complete result, refusing mismatch or output truncation."""

        require_matching_audience(result, self.audience)
        sections: List[str] = []

        persona = result.persona_context
        if persona is not None:
            persona_lines: List[str] = []
            groups = (
                ("Character Authority", persona.authority_items),
                ("Persona Interpretation", persona.interpretation_items),
                ("Approved Relationship Growth", persona.approved_growth_items),
            )
            item_index = 1
            for title, items in groups:
                if not items:
                    continue
                persona_lines.append(f"## {title}")
                for item in items:
                    persona_lines.append(self._persona_line(item, item_index))
                    item_index += 1
            if persona_lines:
                sections.append("# Persona Context\n" + "\n".join(persona_lines))

        relationship = result.relationship_context
        if relationship is not None:
            relationship_items = []
            if relationship.premise is not None:
                relationship_items.append(relationship.premise)
            relationship_items.extend(relationship.narratives)
            if relationship_items:
                lines = [
                    self._relationship_line(item, index)
                    for index, item in enumerate(relationship_items, 1)
                ]
                sections.append("# Relationship Context\n" + "\n".join(lines))

        if result.memories:
            lines = [
                self._memory_line(item, index)
                for index, item in enumerate(result.memories, 1)
            ]
            sections.append("# Relevant Memories\n" + "\n".join(lines))

        if result.events:
            lines = [
                self._event_line(item, index)
                for index, item in enumerate(result.events, 1)
            ]
            sections.append("# Relationship Events\n" + "\n".join(lines))

        if result.signals:
            lines = [
                f"{index}. [{item.signal_type}] {item.summary}"
                for index, item in enumerate(result.signals, 1)
            ]
            sections.append("# Current Recall Signals\n" + "\n".join(lines))

        temporal_lines = []
        if result.temporal_context.observed_at is not None:
            temporal_lines.append(f"Observed at: {result.temporal_context.observed_at}")
        if result.temporal_context.world_time is not None:
            world_time = result.temporal_context.world_time
            temporal_lines.append(
                f"World time [{world_time.clock_id}]: {world_time.display_value}"
            )
        if temporal_lines:
            sections.append("# Temporal Context\n" + "\n".join(temporal_lines))

        if result.notices:
            lines = [
                f"{index}. [{item.severity.value}] {item.code}: {item.message}"
                for index, item in enumerate(result.notices, 1)
            ]
            sections.append("# Recall Notices\n" + "\n".join(lines))

        rendered = "\n\n".join(sections)
        if self.max_output_cost is not None:
            required_cost = self._cost_estimator(rendered)
            if required_cost > self.max_output_cost:
                raise RecallRenderBudgetError(required_cost, self.max_output_cost)
        return rendered
