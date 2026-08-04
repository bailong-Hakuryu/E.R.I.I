import shutil
import tempfile
import unittest

from erii import ERIIEngine
from erii import __version__
from erii.security.sanitizer import SecuritySanitizer


class TestV030Enhancements(unittest.TestCase):
    def test_public_package_version(self):
        self.assertEqual(__version__, "0.4.0")


    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_context_manager_and_close(self):
        with ERIIEngine(storage_dir=self.test_dir) as engine:
            self.assertFalse(engine.archiver_worker.running)
            engine.start()
            self.assertTrue(engine.archiver_worker.running)
        # Should be shut down after context exit
        self.assertFalse(engine.archiver_worker.running)

    def test_user_msg_alias_compatibility(self):
        engine = ERIIEngine(storage_dir=self.test_dir)
        try:
            # Calling with deprecated user_msg kwarg instead of user_message
            engine.remember(
                agent_id="test_agent",
                user_id="白龙",
                user_msg="你好，Lumi",
                bot_reply="你好呀",
            )
        finally:
            engine.close()

    def test_unicode_sanitizer_validation(self):
        valid_user = SecuritySanitizer.validate_key("白龙_Upper", "user_id")
        self.assertEqual(valid_user, "白龙_Upper")

        with self.assertRaises(ValueError):
            SecuritySanitizer.validate_key("../malicious_path", "user_id")


if __name__ == "__main__":
    unittest.main()
