"""Repository-level regression tests for accidental credential commits."""

from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.check_secrets import SECRET_PATTERNS, find_secret_locations


ROOT = Path(__file__).resolve().parents[1]


class RepositorySecretHygieneTests(unittest.TestCase):
    def test_secret_patterns_detect_a_constructed_provider_token(self) -> None:
        fake_token = ("sk-" + ("a" * 32)).encode("ascii")

        self.assertTrue(any(pattern.search(fake_token) for pattern in SECRET_PATTERNS))

    def test_tracked_repository_contains_no_credential_literals(self) -> None:
        self.assertEqual(find_secret_locations(ROOT), ())

    def test_documentation_is_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            token = "sk-" + ("a" * 32)
            (root / "README.md").write_text(
                f"credential example: {token}\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "README.md"], cwd=root, check=True)

            self.assertEqual(
                find_secret_locations(root),
                ((Path("README.md"), 1),),
            )

    def test_labelled_secret_with_punctuation_is_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            password = "p@ssw0rd" + ("1" * 24)
            (root / "config.txt").write_text(
                f'password="{password}"\n',
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "config.txt"], cwd=root, check=True)

            self.assertEqual(
                find_secret_locations(root),
                ((Path("config.txt"), 1),),
            )


if __name__ == "__main__":
    unittest.main()
