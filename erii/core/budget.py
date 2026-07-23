"""Token Budget Manager for E.R.I.I. Engine context prompt assembly.

Follows Google Python Style Guide.
"""

from typing import Dict, List, Optional


class MemoryBudgetManager:
    """Allocates context token budget across core memory, timeline, and dynamic nodes."""

    def __init__(
        self,
        core_budget: int = 300,
        timeline_budget: int = 500,
        dynamic_budget: int = 800,
    ) -> None:
        """Initializes MemoryBudgetManager with token bounds.

        Args:
            core_budget: Maximum character/token budget for Core Persona memory.
            timeline_budget: Maximum character/token budget for Experiential Timeline.
            dynamic_budget: Maximum character/token budget for Dynamic Nodes.
        """
        self.core_budget = core_budget
        self.timeline_budget = timeline_budget
        self.dynamic_budget = dynamic_budget

    @staticmethod
    def _truncate_text(text: str, max_chars: int) -> str:
        """Truncates text safely if it exceeds character budget limit.

        Args:
            text: Input string.
            max_chars: Character length threshold.

        Returns:
            Truncated string.
        """
        if not text or len(text) <= max_chars:
            return text
        return text[: max_chars - 3] + "..."

    def allocate_memory_context(
        self,
        core_memory: str,
        timeline_entries: List[str],
        dynamic_nodes_formatted: str,
    ) -> Dict[str, str]:
        """Allocates context segments strictly adhering to configured budgets.

        Args:
            core_memory: Raw core memory string.
            timeline_entries: List of timeline entry strings.
            dynamic_nodes_formatted: Formatted dynamic nodes text string.

        Returns:
            Dictionary containing budgeted string sections:
            'core_memory', 'timeline_context', 'dynamic_memory'.
        """
        budgeted_core = self._truncate_text(core_memory, self.core_budget)

        # Truncate timeline entries to fit timeline budget
        timeline_text = "\n".join(timeline_entries)
        budgeted_timeline = self._truncate_text(timeline_text, self.timeline_budget)

        budgeted_dynamic = self._truncate_text(
            dynamic_nodes_formatted, self.dynamic_budget
        )

        return {
            "core_memory": budgeted_core,
            "timeline_context": budgeted_timeline,
            "dynamic_memory": budgeted_dynamic,
        }
