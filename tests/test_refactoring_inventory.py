"""Contracts for the generated R0 refactoring inventory."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.refactoring_inventory import DEFAULT_OUTPUT, ROOT, build_inventory, render_inventory


class RefactoringInventoryTests(unittest.TestCase):
    def test_repository_inventory_is_current_and_covers_r1_callers(self) -> None:
        inventory = build_inventory()
        rendered = render_inventory(inventory)

        self.assertEqual(DEFAULT_OUTPUT.read_text(encoding="utf-8"), rendered)
        self.assertGreaterEqual(len(inventory.memory_pack_helpers), 10)
        self.assertIn(
            "analyze_memory_pack",
            {item.name for item in inventory.memory_pack_analysis_functions},
        )
        self.assertNotIn(
            "_validate_temporal_pack",
            {item.name for item in inventory.engine_methods},
        )
        self.assertGreater(len(inventory.calls), 50)
        call_paths = {call.path for call in inventory.calls}
        self.assertIn("erii/server/app.py", call_paths)
        self.assertIn("tests/test_consolidation_memorypack.py", call_paths)
        self.assertIn("tests/test_lifecycle_memory_pack_import_coordinator.py", call_paths)

    def test_public_facades_and_storage_interface_are_present(self) -> None:
        inventory = build_inventory()

        self.assertIn("ERIIEngine", inventory.root_exports)
        self.assertIn("DataLifecycleCoordinator", inventory.root_exports)
        self.assertIn("export_memory", {method.name for method in inventory.engine_methods})
        self.assertIn("execute", {method.name for method in inventory.lifecycle_coordinator_methods})
        self.assertIn("load_nodes", {method.name for method in inventory.storage_methods})

    def test_check_mode_rejects_a_stale_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "inventory.md"
            output.write_text("stale\n", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "refactoring_inventory.py"),
                    "--output",
                    str(output),
                    "--check",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )

        self.assertEqual(completed.returncode, 1)
        self.assertIn("inventory is stale", completed.stdout)


if __name__ == "__main__":
    unittest.main()
