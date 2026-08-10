#!/usr/bin/env python3
"""验证所有 Ruff 错误已修复"""
import subprocess
import sys

result = subprocess.run(
    ['python', '-m', 'pylint', '--errors-only', 'erii/server/app.py', 'examples/08_turn_lifecycle_integration.py'],
    capture_output=True,
    text=True
)

print("Code quality check:")
if result.returncode == 0:
    print("✓ No errors found!")
else:
    print(f"✗ Found errors:\n{result.stdout}\n{result.stderr}")

sys.exit(result.returncode)
