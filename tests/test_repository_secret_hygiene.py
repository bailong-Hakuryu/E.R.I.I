"""Repository-level regression tests for accidental credential commits."""

from pathlib import Path
import unittest

from scripts.check_secrets import SECRET_PATTERNS, find_secret_locations


ROOT = Path(__file__).resolve().parents[1]


class RepositorySecretHygieneTests(unittest.TestCase):
    def test_secret_patterns_detect_a_constructed_provider_token(self) -> None:
        fake_token = ("sk-" + ("a" * 32)).encode("ascii")

        self.assertTrue(any(pattern.search(fake_token) for pattern in SECRET_PATTERNS))

    def test_tracked_repository_contains_no_credential_literals(self) -> None:
        self.assertEqual(find_secret_locations(ROOT), ())


if __name__ == "__main__":
    unittest.main()
