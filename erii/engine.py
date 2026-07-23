"""E.R.I.I. Unified Orchestration Engine (ERIIEngine).

Main entry point for AI Agent long-term memory integration.
Follows Google Python Style Guide.
"""

from typing import Callable, List, Optional, Union

from erii.adapters.base import BaseLLMAdapter
from erii.adapters.custom_adapter import CallableLLMAdapter
from erii.core.archiver import AsyncArchiverWorker
from erii.core.budget import MemoryBudgetManager
from erii.core.decay import MemoryDecayEvaluator
from erii.core.retriever import MemoryRetriever
from erii.models.config import ERIIConfig
from erii.models.node import MemoryNode
from erii.security.sanitizer import SecuritySanitizer
from erii.storage.base import BaseStorage
from erii.storage.file_storage import FileStorage


class DummyMockLLMAdapter(BaseLLMAdapter):
    """Fallback dummy LLM adapter when no LLM is provided."""

    def generate(self, prompt: str) -> str:
        return '{"timeline_entry": "Interaction logged", "impressions": []}'


class ERIIEngine:
    """Experiential Recall & Impression Integration Engine (E.R.I.I.)."""

    def __init__(
        self,
        storage_dir: str = "./erii_memory",
        llm: Optional[Union[BaseLLMAdapter, Callable[[str], str]]] = None,
        storage_driver: Optional[BaseStorage] = None,
        config: Optional[ERIIConfig] = None,
    ) -> None:
        """Initializes ERIIEngine.

        Args:
            storage_dir: Directory path for default file storage.
            llm: LLM provider adapter or Python callable function.
            storage_driver: Custom storage driver instance (optional).
            config: ERIIConfig instance (optional).
        """
        self.config = config or ERIIConfig(storage_dir=storage_dir)

        # 1. Resolve LLM Adapter
        if callable(llm) and not isinstance(llm, BaseLLMAdapter):
            self.llm_adapter: BaseLLMAdapter = CallableLLMAdapter(llm)
        elif isinstance(llm, BaseLLMAdapter):
            self.llm_adapter = llm
        else:
            self.llm_adapter = DummyMockLLMAdapter()

        # 2. Resolve Storage Driver
        if storage_driver is not None:
            self.storage = storage_driver
        else:
            self.storage = FileStorage(root_dir=self.config.storage_dir)

        # 3. Instantiate Sub-engines
        self.decay_evaluator = MemoryDecayEvaluator(
            decay_rate=self.config.decay_rate,
            max_weight_cap=self.config.max_weight_cap,
        )
        self.retriever = MemoryRetriever(decay_rate=self.config.decay_rate)
        self.budget_manager = MemoryBudgetManager(
            core_budget=self.config.core_budget,
            timeline_budget=self.config.timeline_budget,
            dynamic_budget=self.config.dynamic_budget,
        )

        # 4. Instantiate Background Archiver Worker
        self.archiver_worker = AsyncArchiverWorker(
            storage=self.storage,
            llm_adapter=self.llm_adapter,
            enable_sanitizer=self.config.enable_security_sanitizer,
            enable_pii_scrubbing=self.config.enable_pii_scrubbing,
        )

    def remember(
        self,
        agent_id: str,
        user_id: str,
        user_message: str,
        bot_reply: str,
    ) -> None:
        """Records a conversation turn into memory and enqueues archival.

        Args:
            agent_id: Agent identifier.
            user_id: User identifier.
            user_message: User message text string.
            bot_reply: Assistant response text string.
        """
        if not user_message or not bot_reply:
            return

        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")

        if self.config.enable_security_sanitizer:
            user_message = SecuritySanitizer.sanitize_text(user_message)
            bot_reply = SecuritySanitizer.sanitize_text(bot_reply)

        if self.config.enable_pii_scrubbing:
            user_message = SecuritySanitizer.scrub_pii(user_message)
            bot_reply = SecuritySanitizer.scrub_pii(bot_reply)

        # Push turn to background archival worker thread
        if self.config.async_archival:
            self.archiver_worker.push_task(
                agent_id=clean_agent,
                user_id=clean_user,
                user_msg=user_message,
                bot_reply=bot_reply,
            )

    def recall(
        self,
        agent_id: str,
        user_id: str,
        query: str,
        top_k: int = 5,
    ) -> str:
        """Recalls relevant memory context formatted for LLM system prompt injection.

        Applies Security Sanitization, Term Matching, Category Diversity Cap,
        Recall Reinforcement, and Token Budgeting.

        Args:
            agent_id: Agent identifier.
            user_id: User identifier.
            query: Query message string for term matching.
            top_k: Maximum dynamic nodes to retrieve.

        Returns:
            Formatted Markdown context string ready for prompt injection.
        """
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")

        if self.config.enable_security_sanitizer:
            query = SecuritySanitizer.sanitize_text(query)

        # 1. Load memory nodes and run decay evaluation
        nodes = self.storage.load_nodes(clean_agent, clean_user)
        nodes = self.decay_evaluator.sweep_nodes(nodes)

        # 2. Retrieve relevant nodes via Diversity Cap
        selected_nodes = self.retriever.retrieve_relevant_nodes(
            query=query, all_nodes=nodes, top_k=top_k
        )

        # 3. Persist updated node access counts & reinforced scores
        if selected_nodes:
            self.storage.save_nodes(clean_agent, clean_user, nodes)

        # 4. Extract Core Memory & Experiential Timeline
        core_memory = self.storage.get_core_memory(clean_agent, clean_user)
        timeline_entries = self.storage.get_recent_timeline(
            clean_agent, clean_user, limit=4
        )

        # Format dynamic nodes
        dynamic_lines = []
        for idx, node in enumerate(selected_nodes, 1):
            weight = self.decay_evaluator.evaluate_node(node)
            type_tag = f"[{node.node_type.value.upper()}]"
            dynamic_lines.append(
                f"{idx}. {type_tag} {node.content} (weight: {weight:.2f})"
            )
        dynamic_formatted = "\n".join(dynamic_lines)

        # 5. Apply Token Budget Allocator
        budgeted = self.budget_manager.allocate_memory_context(
            core_memory=core_memory,
            timeline_entries=timeline_entries,
            dynamic_nodes_formatted=dynamic_formatted,
        )

        # Assemble final prompt sections
        sections = []
        if budgeted["core_memory"]:
            sections.append(f"# Core Persona Memory\n{budgeted['core_memory']}")
        if budgeted["dynamic_memory"]:
            sections.append(f"# Relevant Memories\n{budgeted['dynamic_memory']}")
        if budgeted["timeline_context"]:
            sections.append(f"# Experiential Timeline\n{budgeted['timeline_context']}")

        return "\n\n".join(sections)

    def set_core_memory(self, agent_id: str, user_id: str, content: str) -> None:
        """Sets Core Persona memory string for given agent and user."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")
        self.storage.save_core_memory(clean_agent, clean_user, content)

    def get_core_memory(self, agent_id: str, user_id: str) -> str:
        """Gets Core Persona memory string for given agent and user."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")
        return self.storage.get_core_memory(clean_agent, clean_user)

    def close(self) -> None:
        """Gracefully shuts down background archiver thread."""
        if hasattr(self, "archiver_worker"):
            self.archiver_worker.shutdown()
