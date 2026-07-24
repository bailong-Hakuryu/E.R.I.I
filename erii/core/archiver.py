"""Async Memory Archiver Worker for E.R.I.I. Engine.

Extracts facts, persona impressions, and experiential timeline events in background threads.
Follows Google Python Style Guide.
"""

import json
import logging
import queue
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Union

from erii.adapters.base import BaseLLMAdapter
from erii.core.queue.base import ArchivalTask, BaseTaskQueue
from erii.core.queue.persistent_queue import PersistentTaskQueue
from erii.models.node import MemoryNode, MemoryState, MemoryType
from erii.security.sanitizer import SecuritySanitizer
from erii.storage.base import BaseStorage

logger = logging.getLogger("erii")


class AsyncArchiverWorker:
    """Background worker for extracting impressions, inner monologue thoughts, and timeline events via LLM."""

    EXTRACTION_PROMPT = """You are an AI Memory Extraction Engine. Analyze the following conversation turn and extract structured long-term memories, first-person experience timeline entries, and first-person inner monologue/thought entries.

CRITICAL PERSPECTIVE, IDENTITY & LANGUAGE REQUIREMENTS:
1. STRICT FIRST-PERSON PERSPECTIVE (MANDATORY):
   - The Assistant in this turn is the AI Character/Agent '{agent_id}'.
   - The User in this turn is the Human User '{user_id}'.
   - All extracted memory items ("impressions[].content", "timeline_entry", "thought_entry.content") MUST be written strictly from Assistant's ('{agent_id}') FIRST-PERSON PERSPECTIVE.
   - When referring to Assistant herself, use '我' (I/me) or her character name '{agent_id}'.
   - When referring to User, use '{user_id}' or '用户' or 'Sakura'. NEVER use '我' to refer to User!
   - Example CORRECT impression: "我向 {user_id} 道晚安，希望他做个好梦。" (Assistant said good night to User)
   - Example INCORRECT impression: "我向 绘梨衣 道晚安" (WRONG! Do not write from User's perspective!)

2. MANDATORY LANGUAGE RULE (STRICTLY ENFORCED):
   - If the conversation is in Chinese (中文), ALL output fields MUST be written in Chinese (中文). Absolutely NO English allowed in any field.
   - If the conversation is in English, output in English. Match the language of the conversation exactly.
   - DEFAULT: If in doubt, use Chinese (中文).

3. TEMPORAL ANCHORING: When user or assistant mentions relative time expressions ("tomorrow", "yesterday", "明天", "昨天"), preserve temporal context clearly.

Conversation Turn:
User ({user_id}): {user_msg}
Assistant ({agent_id}): {bot_reply}

Output strictly valid JSON with no markdown block formatting:
{{
  "timeline_entry": "First-person experiential summary of this interaction strictly from Assistant's ('{agent_id}') perspective in the conversation's language",
  "thought_entry": {{
    "content": "First-person unspoken inner psychological monologue or reflection strictly from Assistant's ('{agent_id}') perspective in the conversation's language",
    "visibility": "public_log|internal_monologue",
    "is_unresolved": false,
    "emotional_score": 0.0,
    "foreshadowing_tags": ["tag1", "tag2"]
  }},
  "impressions": [
    {{
      "type": "fact|preference|event|emotion|relationship|thought|diary",
      "content": "Memory item content strictly from Assistant's ('{agent_id}') perspective ('我' = Assistant '{agent_id}', '{user_id}' = User)",
      "base_importance": 0.1 to 1.0,
      "emotional_score": -1.0 to 1.0,
      "tags": ["tag1", "tag2"]
    }}
  ]
}}
"""

    def __init__(
        self,
        storage: BaseStorage,
        llm_adapter: BaseLLMAdapter,
        enable_sanitizer: bool = True,
        enable_pii_scrubbing: bool = True,
        task_queue: Optional[BaseTaskQueue] = None,
    ) -> None:
        """Initializes AsyncArchiverWorker.

        Args:
            storage: Storage driver instance.
            llm_adapter: LLM adapter instance.
            enable_sanitizer: Anti-injection flag.
            enable_pii_scrubbing: PII scrubbing flag.
            task_queue: BaseTaskQueue instance (optional).
        """
        self.storage = storage
        self.llm_adapter = llm_adapter
        self.enable_sanitizer = enable_sanitizer
        self.enable_pii_scrubbing = enable_pii_scrubbing
        self.task_queue = task_queue or PersistentTaskQueue()

        self.running = True
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()

    def push_task(
        self, agent_id: str, user_id: str, user_msg: str, bot_reply: str
    ) -> str:
        """Enqueues conversation turn for background archival.

        Args:
            agent_id: Agent identifier.
            user_id: User identifier.
            user_msg: User message text.
            bot_reply: Bot response text.

        Returns:
            String task_id.
        """
        return self.task_queue.enqueue(agent_id, user_id, user_msg, bot_reply)

    def shutdown(self) -> None:
        """Stops worker thread gracefully."""
        self.running = False
        if hasattr(self, "worker_thread") and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=1.0)

    def close(self) -> None:
        """Alias for shutdown()."""
        self.shutdown()

    def __enter__(self) -> "AsyncArchiverWorker":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit."""
        self.shutdown()

    def _worker_loop(self) -> None:
        """Main queue consumer loop running in background thread."""
        while self.running:
            try:
                task = self.task_queue.dequeue()
                if task is None:
                    time.sleep(0.2)
                    continue
                try:
                    self._process_archival(task)
                    self.task_queue.complete(task.task_id)
                except Exception as ex:
                    logger.error("Error processing archival task %s: %s", task.task_id, str(ex))
                    self.task_queue.fail(task.task_id, str(ex))
            except Exception as e:
                logger.error("Error in AsyncArchiverWorker loop: %s", str(e))
                time.sleep(0.5)

    def _process_archival(self, task: Union[ArchivalTask, Dict[str, str]]) -> None:
        """Executes LLM extraction and persists memory nodes & timeline entries."""
        if isinstance(task, ArchivalTask):
            agent_id = task.agent_id
            user_id = task.user_id
            user_msg = task.user_msg
            bot_reply = task.bot_reply
        else:
            agent_id = task["agent_id"]
            user_id = task["user_id"]
            user_msg = task["user_msg"]
            bot_reply = task["bot_reply"]

        prompt = self.EXTRACTION_PROMPT.format(
            agent_id=agent_id,
            user_id=user_id,
            user_msg=user_msg,
            bot_reply=bot_reply
        )

        try:
            raw_response = self.llm_adapter.generate(prompt)
            if not raw_response:
                return

            # Clean JSON markdown fences if present
            clean_json_str = raw_response.strip()
            if clean_json_str.startswith("```json"):
                clean_json_str = clean_json_str[7:]
            if clean_json_str.startswith("```"):
                clean_json_str = clean_json_str[3:]
            if clean_json_str.endswith("```"):
                clean_json_str = clean_json_str[:-3]
            clean_json_str = clean_json_str.strip()

            parsed = json.loads(clean_json_str)

            # 1. Process Experiential Timeline Entry
            timeline_entry = parsed.get("timeline_entry")
            if timeline_entry:
                if self.enable_sanitizer:
                    timeline_entry = SecuritySanitizer.sanitize_text(timeline_entry)
                if self.enable_pii_scrubbing:
                    timeline_entry = SecuritySanitizer.scrub_pii(timeline_entry)
                self.storage.add_timeline_entry(agent_id, user_id, timeline_entry)

            existing_nodes = self.storage.load_nodes(agent_id, user_id)
            new_nodes: List[MemoryNode] = []

            # 2. Process Inner Monologue Thought Entry
            thought_entry = parsed.get("thought_entry")
            if thought_entry and isinstance(thought_entry, dict):
                thought_content = thought_entry.get("content", "").strip()
                if thought_content:
                    if self.enable_sanitizer:
                        thought_content = SecuritySanitizer.sanitize_text(thought_content)
                    if self.enable_pii_scrubbing:
                        thought_content = SecuritySanitizer.scrub_pii(thought_content)

                    visibility = thought_entry.get("visibility", "public_log")
                    if visibility not in ("public_log", "internal_monologue"):
                        visibility = "public_log"

                    thought_node = MemoryNode(
                        node_id=str(uuid.uuid4()),
                        user_id=user_id,
                        agent_id=agent_id,
                        node_type=MemoryType.THOUGHT,
                        content=thought_content,
                        tags=thought_entry.get("foreshadowing_tags", []),
                        base_importance=0.8,
                        emotional_score=float(thought_entry.get("emotional_score", 0.0)),
                        visibility=visibility,
                        is_unresolved=bool(thought_entry.get("is_unresolved", False)),
                        foreshadowing_tags=thought_entry.get("foreshadowing_tags", []),
                    )
                    new_nodes.append(thought_node)

            # 3. Process Impressions Memory Nodes
            impressions = parsed.get("impressions", [])
            for item in impressions:
                if not isinstance(item, dict):
                    continue
                raw_type = item.get("type", "fact").lower()
                # Security Guard: Intercept and drop INSTRUCTION type nodes
                if raw_type == "instruction":
                    logger.warning("Security Warning: INSTRUCTION node intercepted and disarmed.")
                    continue

                try:
                    mem_type = MemoryType(raw_type)
                except ValueError:
                    mem_type = MemoryType.FACT

                content = item.get("content", "").strip()
                if not content:
                    continue

                if self.enable_sanitizer:
                    content = SecuritySanitizer.sanitize_text(content)
                if self.enable_pii_scrubbing:
                    content = SecuritySanitizer.scrub_pii(content)

                node = MemoryNode(
                    node_id=str(uuid.uuid4()),
                    user_id=user_id,
                    agent_id=agent_id,
                    node_type=mem_type,
                    content=content,
                    tags=item.get("tags", []),
                    base_importance=float(item.get("base_importance", 0.5)),
                    emotional_score=float(item.get("emotional_score", 0.0)),
                    visibility=item.get("visibility", "public_log"),
                    is_unresolved=bool(item.get("is_unresolved", False)),
                    foreshadowing_tags=item.get("foreshadowing_tags", []),
                )
                new_nodes.append(node)

            if new_nodes:
                all_nodes = existing_nodes + new_nodes
                self.storage.save_nodes(agent_id, user_id, all_nodes)

        except Exception as e:
            logger.error("Failed to parse LLM memory extraction JSON for %s/%s: %s", agent_id, user_id, str(e))
