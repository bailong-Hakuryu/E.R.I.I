"""Async Memory Archiver Worker for E.R.I.I. Engine.

Extracts facts, persona impressions, and experiential timeline events in background threads.
Follows Google Python Style Guide.
"""

import json
import logging
import queue
import threading
import uuid
from typing import Callable, Dict, List, Optional

from erii.adapters.base import BaseLLMAdapter
from erii.models.node import MemoryNode, MemoryState, MemoryType
from erii.security.sanitizer import SecuritySanitizer
from erii.storage.base import BaseStorage

logger = logging.getLogger("erii")


class AsyncArchiverWorker:
    """Background worker for extracting impressions and timeline events via LLM."""

    EXTRACTION_PROMPT = """You are an AI Memory Extraction Engine. Analyze the following conversation turn and extract structured long-term memories and first-person experience timeline entries.

Conversation Turn:
User: {user_msg}
Assistant: {bot_reply}

Output strictly valid JSON with no markdown block formatting:
{{
  "timeline_entry": "First-person experiential summary of this interaction from Assistant's perspective (e.g. 'I discussed favorite tea with Bob.')",
  "impressions": [
    {{
      "type": "fact|preference|event|emotion|relationship",
      "content": "Specific memory item content",
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
    ) -> None:
        """Initializes AsyncArchiverWorker.

        Args:
            storage: Storage driver instance.
            llm_adapter: LLM adapter instance.
            enable_sanitizer: Anti-injection flag.
            enable_pii_scrubbing: PII scrubbing flag.
        """
        self.storage = storage
        self.llm_adapter = llm_adapter
        self.enable_sanitizer = enable_sanitizer
        self.enable_pii_scrubbing = enable_pii_scrubbing

        self.queue: queue.Queue = queue.Queue()
        self.running = True
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()

    def push_task(
        self, agent_id: str, user_id: str, user_msg: str, bot_reply: str
    ) -> None:
        """Enqueues conversation turn for background archival.

        Args:
            agent_id: Agent identifier.
            user_id: User identifier.
            user_msg: User message text.
            bot_reply: Bot response text.
        """
        self.queue.put({
            "agent_id": agent_id,
            "user_id": user_id,
            "user_msg": user_msg,
            "bot_reply": bot_reply,
        })

    def shutdown(self) -> None:
        """Stops worker thread gracefully."""
        self.running = False
        self.queue.put(None)

    def _worker_loop(self) -> None:
        """Main queue consumer loop running in background thread."""
        while self.running:
            try:
                task = self.queue.get(timeout=1.0)
                if task is None:
                    break
                self._process_archival(task)
                self.queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error("Error in AsyncArchiverWorker loop: %s", str(e))

    def _process_archival(self, task: Dict[str, str]) -> None:
        """Executes LLM extraction and persists memory nodes & timeline entries."""
        agent_id = task["agent_id"]
        user_id = task["user_id"]
        user_msg = task["user_msg"]
        bot_reply = task["bot_reply"]

        prompt = self.EXTRACTION_PROMPT.format(user_msg=user_msg, bot_reply=bot_reply)

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

            # 2. Process Impressions Memory Nodes
            impressions = parsed.get("impressions", [])
            if not impressions:
                return

            existing_nodes = self.storage.load_nodes(agent_id, user_id)
            new_nodes: List[MemoryNode] = []

            for item in impressions:
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
                )
                new_nodes.append(node)

            if new_nodes:
                all_nodes = existing_nodes + new_nodes
                self.storage.save_nodes(agent_id, user_id, all_nodes)

        except Exception as e:
            logger.error("Failed to parse LLM memory extraction JSON for %s/%s: %s", agent_id, user_id, str(e))
