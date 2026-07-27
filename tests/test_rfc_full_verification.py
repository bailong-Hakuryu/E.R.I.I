import os
import shutil
import tempfile
import unittest

from erii import ERIIEngine, MemoryNode, SQLiteStorage
from erii.security.sanitizer import SecuritySanitizer
from erii.storage.file_storage import FileStorage


class TestRFCFullVerification(unittest.TestCase):
    """RFC v0.3.0 5大核心问题全面诊断与验证测试套件"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_verification.db")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_rfc1_unicode_sanitizer_and_path_hash(self):
        """验证 RFC 1: Unicode (中文/日文) 校验支持与路径隔离"""
        # 1. 验证中文/日文 key 不再报错
        agent_id = "agent_lumi_avatar"
        user_id = "白龙_User"
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")
        self.assertEqual(clean_agent, agent_id)
        self.assertEqual(clean_user, user_id)

        # 2. 验证路径遍历被拦截
        with self.assertRaises(ValueError):
            SecuritySanitizer.validate_key("../etc/passwd", "user_id")

        # 3. 验证 FileStorage 安全哈希物理目录
        storage = FileStorage(root_dir=self.test_dir)
        user_dir = storage._get_user_dir(agent_id, user_id)
        self.assertTrue(os.path.exists(user_dir))
        # 包含 SHA256 哈希安全后缀
        self.assertIn("_", os.path.basename(user_dir))

    def test_rfc2_temporal_and_language_prompt_anchoring(self):
        """验证 RFC 2: 语言与时间锚定约束 Prompt 及 created_at 结构体时间"""
        engine = ERIIEngine(storage_dir=self.test_dir)
        try:
            # 检查 prompt 包含语言与时间锚定说明
            prompt = engine.archiver_worker.EXTRACTION_PROMPT
            self.assertTrue("CRITICAL PERSPECTIVE, IDENTITY & LANGUAGE REQUIREMENTS" in prompt or "CRITICAL LANGUAGE & TEMPORAL REQUIREMENTS" in prompt)
            self.assertIn("TEMPORAL ANCHORING", prompt)
        finally:
            engine.close()

    def test_rfc3_transaction_diff_full_sync(self):
        """验证 RFC 3: SQLiteStorage.save_nodes 事务级 Diff 物理清理被删除节点"""
        storage = SQLiteStorage(db_path=self.db_path)
        agent_id = "agent_lumi"
        user_id = "bob"

        # 1. 保存 2 个节点
        node1 = MemoryNode(node_id="node_1", user_id=user_id, agent_id=agent_id, content="喜欢公园")
        node2 = MemoryNode(node_id="node_2", user_id=user_id, agent_id=agent_id, content="喜欢甜食")
        storage.save_nodes(agent_id, user_id, [node1, node2])

        loaded = storage.load_nodes(agent_id, user_id)
        self.assertEqual(len(loaded), 2)

        # 2. 仅保留 node1 保存（模拟删除 node2）
        storage.save_nodes(agent_id, user_id, [node1])

        # 3. 验证 node2 已从 SQLite 物理删除
        loaded_after = storage.load_nodes(agent_id, user_id)
        self.assertEqual(len(loaded_after), 1)
        self.assertEqual(loaded_after[0].node_id, "node_1")

    def test_rfc4_context_manager_and_graceful_shutdown(self):
        """验证 RFC 4: ContextManager (__enter__ / __exit__) 自动化资源回收"""
        with ERIIEngine(storage_dir=self.test_dir) as engine:
            self.assertTrue(engine.archiver_worker.running)

        # 退出 with 块后自动 shutdown
        self.assertFalse(engine.archiver_worker.running)

    def test_rfc5_api_signature_alias_compatibility(self):
        """验证 RFC 5: user_msg 别名兼容入参"""
        with ERIIEngine(storage_dir=self.test_dir) as engine:
            # 使用 user_msg 参数
            engine.remember(
                agent_id="agent_lumi",
                user_id="白龙",
                user_msg="明天去看海吗？",
                bot_reply="好呀！",
            )


if __name__ == "__main__":
    unittest.main()
