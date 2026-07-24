"""E.R.I.I. Unified Orchestration Engine (ERIIEngine).

Main entry point for AI Agent long-term memory integration.
Follows Google Python Style Guide.
"""

from typing import Any, Callable, Dict, List, Optional, Union

from erii.adapters.base import BaseLLMAdapter
from erii.adapters.custom_adapter import CallableLLMAdapter
from erii.core.archiver import AsyncArchiverWorker
from erii.core.budget import MemoryBudgetManager
from erii.core.decay import MemoryDecayEvaluator
from erii.core.retriever import MemoryRetriever
from erii.core.queue.base import BaseTaskQueue
from erii.models.config import ERIIConfig
from erii.models.node import MemoryNode, MemoryType, MemoryVisibility
from erii.models.pack import MemoryPack
from erii.security.sanitizer import SecuritySanitizer
from erii.storage.base import BaseStorage
from erii.storage.file_storage import FileStorage
from erii.vector.base import BaseEmbeddingProvider, BaseVectorStore
from erii.vector.in_memory_vector import CallableEmbeddingAdapter, DummyEmbeddingProvider


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
        task_queue: Optional[BaseTaskQueue] = None,
        vector_store: Optional[BaseVectorStore] = None,
        embedding_provider: Optional[Union[BaseEmbeddingProvider, Callable[[str], List[float]]]] = None,
    ) -> None:
        """Initializes ERIIEngine.

        Args:
            storage_dir: Directory path for default file storage.
            llm: LLM provider adapter or Python callable function.
            storage_driver: Custom storage driver instance (optional).
            config: ERIIConfig instance (optional).
            task_queue: Custom BaseTaskQueue implementation (optional).
            vector_store: BaseVectorStore instance for hybrid vector search (optional).
            embedding_provider: BaseEmbeddingProvider or callable function (optional).
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

        # 3. Resolve Vector & Embedding Components
        self.vector_store = vector_store
        if callable(embedding_provider) and not isinstance(embedding_provider, BaseEmbeddingProvider):
            self.embedding_provider: Optional[BaseEmbeddingProvider] = CallableEmbeddingAdapter(embedding_provider)
        elif isinstance(embedding_provider, BaseEmbeddingProvider):
            self.embedding_provider = embedding_provider
        elif self.vector_store is not None:
            self.embedding_provider = DummyEmbeddingProvider()
        else:
            self.embedding_provider = None

        # 4. Instantiate Sub-engines
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

        # 5. Instantiate Background Archiver Worker
        self.archiver_worker = AsyncArchiverWorker(
            storage=self.storage,
            llm_adapter=self.llm_adapter,
            enable_sanitizer=self.config.enable_security_sanitizer,
            enable_pii_scrubbing=self.config.enable_pii_scrubbing,
            task_queue=task_queue,
        )

    def remember(
        self,
        agent_id: str,
        user_id: str,
        user_message: str = "",
        bot_reply: str = "",
        user_msg: str = "",
    ) -> None:
        """Records a conversation turn into memory and enqueues archival.

        Args:
            agent_id: Agent identifier.
            user_id: User identifier.
            user_message: User message text string.
            bot_reply: Assistant response text string.
            user_msg: Deprecated alias for user_message.
        """
        user_message = user_message or user_msg
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

        # 2. Retrieve relevant nodes via RRF Hybrid Search & Diversity Cap
        selected_nodes = self.retriever.retrieve_relevant_nodes(
            query=query,
            all_nodes=nodes,
            top_k=top_k,
            vector_store=self.vector_store,
            embedding_provider=self.embedding_provider,
        )

        # 3. Persist updated node access counts & reinforced scores
        if selected_nodes:
            self.storage.save_nodes(clean_agent, clean_user, nodes)

        # 4. Extract Core Memory & Experiential Timeline
        core_memory = self.storage.get_core_memory(clean_agent, clean_user)
        timeline_entries = self.storage.get_recent_timeline(
            clean_agent, clean_user, limit=4
        )

        # Format dynamic nodes with creation timestamp anchoring
        dynamic_lines = []
        for idx, node in enumerate(selected_nodes, 1):
            weight = self.decay_evaluator.evaluate_node(node)
            type_tag = f"[{node.node_type.value.upper()}]"
            time_prefix = f"[{node.created_at}] " if hasattr(node, "created_at") and node.created_at else ""
            dynamic_lines.append(
                f"{idx}. {time_prefix}{type_tag} {node.content} (weight: {weight:.2f})"
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

    def remember_thought(
        self,
        agent_id: str,
        user_id: str,
        content: str,
        visibility: str = MemoryVisibility.PUBLIC_LOG.value,
        is_unresolved: bool = False,
        emotional_score: float = 0.0,
        foreshadowing_tags: Optional[List[str]] = None,
        created_at: Optional[str] = None,
    ) -> MemoryNode:
        """Explicitly records a first-person inner monologue or diary entry.

        Args:
            agent_id: Agent identifier.
            user_id: User identifier.
            content: Inner monologue or diary text.
            visibility: MemoryVisibility string ("public_log" or "internal_monologue").
            is_unresolved: True if this represents an active narrative suspense/unresolved thought.
            emotional_score: Score between -1.0 and 1.0.
            foreshadowing_tags: List of narrative tags.
            created_at: Custom ISO/world timestamp.

        Returns:
            The created MemoryNode instance.
        """
        import uuid
        from datetime import datetime

        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")

        if self.config.enable_security_sanitizer:
            content = SecuritySanitizer.sanitize_text(content)
        if self.config.enable_pii_scrubbing:
            content = SecuritySanitizer.scrub_pii(content)

        ts = created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        valid_visibility = (
            visibility
            if visibility in (MemoryVisibility.PUBLIC_LOG.value, MemoryVisibility.INTERNAL_MONOLOGUE.value)
            else MemoryVisibility.PUBLIC_LOG.value
        )

        node = MemoryNode(
            node_id=str(uuid.uuid4()),
            user_id=clean_user,
            agent_id=clean_agent,
            node_type=MemoryType.THOUGHT,
            content=content,
            tags=foreshadowing_tags or [],
            base_importance=0.8,
            emotional_score=emotional_score,
            visibility=valid_visibility,
            is_unresolved=is_unresolved,
            foreshadowing_tags=foreshadowing_tags or [],
            created_at=ts,
            last_accessed_at=ts,
        )

        nodes = self.storage.load_nodes(clean_agent, clean_user)
        nodes.append(node)
        self.storage.save_nodes(clean_agent, clean_user, nodes)
        return node

    def get_inner_monologue(
        self,
        agent_id: str,
        user_id: str,
        limit: int = 10,
        unresolved_only: bool = False,
        visibility: Optional[str] = MemoryVisibility.PUBLIC_LOG.value,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieves psychological monologue and diary entries filtered by visibility and narrative suspense."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")

        nodes = self.storage.load_nodes(clean_agent, clean_user)
        filtered = []

        for node in nodes:
            if node.node_type not in (MemoryType.THOUGHT, MemoryType.DIARY):
                continue

            if visibility and node.visibility != visibility:
                continue

            if unresolved_only and not node.is_unresolved:
                continue

            if start_time and node.created_at < start_time:
                continue

            if end_time and node.created_at > end_time:
                continue

            filtered.append(node)

        # Sort: unresolved suspense nodes first (1 > 0), then by created_at descending
        filtered.sort(key=lambda n: (1 if n.is_unresolved else 0, n.created_at), reverse=True)

        result = []
        for node in filtered[:limit]:
            d = node.to_dict()
            d["effective_weight"] = node.calculate_effective_weight(
                decay_rate=self.config.decay_rate,
                max_weight_cap=self.config.max_weight_cap,
            )
            result.append(d)

        return result

    def get_diary_timeline(
        self,
        agent_id: str,
        user_id: str,
        limit: int = 10,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieves formatted first-person public diary timeline entries for UI rendering."""
        return self.get_inner_monologue(
            agent_id=agent_id,
            user_id=user_id,
            limit=limit,
            unresolved_only=False,
            visibility=MemoryVisibility.PUBLIC_LOG.value,
            start_time=start_time,
            end_time=end_time,
        )

    def resolve_thought(
        self,
        agent_id: str,
        user_id: str,
        node_id: str,
    ) -> bool:
        """Marks a suspenseful/unresolved thought node as resolved."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")

        nodes = self.storage.load_nodes(clean_agent, clean_user)
        found = False
        for node in nodes:
            if node.node_id == node_id:
                node.is_unresolved = False
                found = True
                break

        if found:
            self.storage.save_nodes(clean_agent, clean_user, nodes)

        return found

    def export_memory(
        self, agent_id: str, user_id: str, export_path: Optional[str] = None
    ) -> MemoryPack:
        """Exports memory for specified agent and user into a MemoryPack object."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")

        nodes = self.storage.load_nodes(clean_agent, clean_user)
        core_mem = self.storage.get_core_memory(clean_agent, clean_user)
        timeline = self.storage.get_recent_timeline(clean_agent, clean_user, limit=1000)

        raw_timeline = []
        for line in timeline:
            if line.startswith("[") and "]" in line:
                idx = line.index("]")
                ts = line[1:idx]
                content = line[idx + 2 :]
                raw_timeline.append({"timestamp": ts, "content": content})
            else:
                raw_timeline.append({"timestamp": "", "content": line})

        pack = MemoryPack(
            agent_id=clean_agent,
            user_id=clean_user,
            core_memory=core_mem,
            nodes=nodes,
            timeline=raw_timeline,
        )

        if export_path:
            with open(export_path, "w", encoding="utf-8") as f:
                f.write(pack.to_json())

        return pack

    def import_memory(
        self,
        pack_or_path: Union[MemoryPack, str, Dict[str, Any]],
        agent_id: Optional[str] = None,
        user_id: Optional[str] = None,
        overwrite: bool = False,
    ) -> MemoryPack:
        """Imports a MemoryPack object or JSON file into storage."""
        if isinstance(pack_or_path, str):
            with open(pack_or_path, "r", encoding="utf-8") as f:
                pack = MemoryPack.from_json(f.read())
        elif isinstance(pack_or_path, dict):
            pack = MemoryPack.from_dict(pack_or_path)
        else:
            pack = pack_or_path

        target_agent = agent_id or pack.agent_id
        target_user = user_id or pack.user_id

        clean_agent = SecuritySanitizer.validate_key(target_agent, "agent_id")
        clean_user = SecuritySanitizer.validate_key(target_user, "user_id")

        if overwrite:
            existing_nodes = []
        else:
            existing_nodes = self.storage.load_nodes(clean_agent, clean_user)

        node_map = {n.node_id: n for n in existing_nodes}
        for n in pack.nodes:
            node_map[n.node_id] = n

        self.storage.save_nodes(clean_agent, clean_user, list(node_map.values()))

        if pack.core_memory and (overwrite or not self.storage.get_core_memory(clean_agent, clean_user)):
            self.storage.save_core_memory(clean_agent, clean_user, pack.core_memory)

        for entry in pack.timeline:
            self.storage.add_timeline_entry(
                clean_agent, clean_user, entry.get("content", ""), entry.get("timestamp")
            )

        return pack

    def close(self) -> None:
        """Gracefully shuts down background archiver thread."""
        if hasattr(self, "archiver_worker"):
            self.archiver_worker.shutdown()

    def shutdown(self) -> None:
        """Alias for close()."""
        self.close()

    def __enter__(self) -> "ERIIEngine":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit, ensures close() is called."""
        self.close()

