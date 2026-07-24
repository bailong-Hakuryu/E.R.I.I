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

        storage = SQLiteStorage(db_path=db_path)
        queue = PersistentTaskQueue(db_path=db_path)
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
            "timeline_entry": "我与 白龙 确认了他安全到达。",
            "thought_entry": {
                "content": "祝福 白龙 今晚能做个好梦。",
                "visibility": "public_log"
            },
            "impressions": [
                {
                    "type": "event",
                    "content": "我向 白龙 道晚安。",
                    "base_importance": 0.8
                }
            ]
        })
        worker.llm_adapter.generate.return_value = mock_llm_json

        task = {
            "agent_id": "Uesugi_Erii",
            "user_id": "白龙",
            "user_msg": "到家了",
            "bot_reply": "晚安啦Sakura"
        }

        worker._process_archival(task)

        # Assert prompt includes agent_id and user_id perspective rules
        prompt_arg = worker.llm_adapter.generate.call_args[0][0]
        self.assertIn("Uesugi_Erii", prompt_arg)
        self.assertIn("白龙", prompt_arg)
        self.assertIn("STRICT FIRST-PERSON PERSPECTIVE", prompt_arg)

        # Assert saved nodes contain character perspective content
        save_nodes_call = worker.storage.save_nodes.call_args
        self.assertIsNotNone(save_nodes_call)
        agent_id_arg, user_id_arg, saved_nodes = save_nodes_call[0]
        self.assertEqual(agent_id_arg, "Uesugi_Erii")
        self.assertEqual(user_id_arg, "白龙")
        self.assertTrue(len(saved_nodes) > 0)
        self.assertIn("白龙", saved_nodes[0].content)

if __name__ == "__main__":
    unittest.main()
