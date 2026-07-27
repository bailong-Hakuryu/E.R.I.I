import os
import tempfile
import unittest
import json
from unittest.mock import MagicMock

from erii.core.archiver import AsyncArchiverWorker
from erii.storage.sqlite_storage import SQLiteStorage
from erii.core.queue.persistent_queue import PersistentTaskQueue

class TestPerspectiveAndDatabaseAutoCreation(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="erii_standalone_test_")

    def test_database_directory_auto_creation(self):
        """Verify SQLiteStorage and PersistentTaskQueue auto-create parent directories."""
        db_dir = os.path.join(self.test_dir, "nested", "deep", "dir")
        db_path = os.path.join(db_dir, "erii_memory.db")
        
        self.assertFalse(os.path.exists(db_dir))

        SQLiteStorage(db_path=db_path)
        PersistentTaskQueue(db_path=db_path)
        self.assertTrue(os.path.exists(db_dir))

    def test_extraction_prompt_perspective_grounding(self):
        """Verify EXTRACTION_PROMPT grounds agent_id, user_id, and perspective rules."""
        worker = AsyncArchiverWorker.__new__(AsyncArchiverWorker)
        worker.enable_sanitizer = False
        worker.enable_pii_scrubbing = False
        worker.storage = MagicMock()
        worker.storage.load_nodes.return_value = []
        worker.llm_adapter = MagicMock()

        mock_llm_json = json.dumps({
            "timeline_entry": "我与 user_chen 确认了他安全到达。",
            "thought_entry": {
                "content": "祝福 user_chen 今晚能做个好梦。",
                "visibility": "public_log"
            },
            "impressions": [
                {
                    "type": "event",
                    "content": "我向 user_chen 道晚安。",
                    "base_importance": 0.8
                }
            ]
        })
        worker.llm_adapter.generate.return_value = mock_llm_json

        task = {
            "agent_id": "agent_lumi",
            "user_id": "user_chen",
            "user_msg": "到家了",
            "bot_reply": "晚安，祝你做个好梦。"
        }

        worker._process_archival(task)

        # Assert prompt includes agent_id and user_id perspective rules
        prompt_arg = worker.llm_adapter.generate.call_args[0][0]
        self.assertIn("agent_lumi", prompt_arg)
        self.assertIn("user_chen", prompt_arg)
        self.assertIn("STRICT FIRST-PERSON PERSPECTIVE", prompt_arg)

        # Assert saved nodes contain character perspective content
        save_nodes_call = worker.storage.save_nodes.call_args
        self.assertIsNotNone(save_nodes_call)
        agent_id_arg, user_id_arg, saved_nodes = save_nodes_call[0]
        self.assertEqual(agent_id_arg, "agent_lumi")
        self.assertEqual(user_id_arg, "user_chen")
        self.assertTrue(len(saved_nodes) > 0)
        self.assertIn("user_chen", saved_nodes[0].content)

    def test_extraction_with_think_tags_and_markdown_blocks(self):
        """Parse JSON wrapped in model reasoning tags and Markdown fences."""
        worker = AsyncArchiverWorker.__new__(AsyncArchiverWorker)
        worker.enable_sanitizer = False
        worker.enable_pii_scrubbing = False
        worker.storage = MagicMock()
        worker.storage.load_nodes.return_value = []
        worker.llm_adapter = MagicMock()

        raw_llm_response = """<think>
Reasoning chain from a local model...
The user said good night.
</think>
```json
{
  "timeline_entry": "我向 user_chen 道晚安。",
  "thought_entry": {
    "content": "希望 user_chen 做个好梦。",
    "visibility": "public_log"
  },
  "impressions": []
}
```"""
        worker.llm_adapter.generate.return_value = raw_llm_response

        task = {
            "agent_id": "agent_lumi",
            "user_id": "user_chen",
            "user_msg": "晚安",
            "bot_reply": "晚安",
        }

        worker._process_archival(task)

        worker.storage.add_timeline_entry.assert_called_once_with(
            "agent_lumi", "user_chen", "我向 user_chen 道晚安。"
        )

if __name__ == "__main__":
    unittest.main()
