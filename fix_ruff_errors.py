#!/usr/bin/env python3
"""
自动修复 Ruff 代码质量问题
"""

import re
import sys


def fix_unused_exc_variables(file_path):
    """移除未使用的 exc 变量"""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 修复 "as exc:" 为 ":"
    patterns = [
        (r'except RelationshipNotFoundError as exc:', 'except RelationshipNotFoundError:'),
        (r'except TurnNotFoundError as exc:', 'except TurnNotFoundError:'),
        (r'except TurnConflictError as exc:', 'except TurnConflictError:'),
        (r'except TurnTerminalConflictError as exc:', 'except TurnTerminalConflictError:'),
        (r'except \(RelationshipNotFoundError, TurnNotFoundError\) as exc:', 'except (RelationshipNotFoundError, TurnNotFoundError):'),
    ]

    for pattern, replacement in patterns:
        content = re.sub(pattern, replacement, content)

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"OK Fixed: {file_path}")


def fix_unused_imports(file_path):
    """移除未使用的导入"""
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    new_lines = []
    for line in lines:
        # 跳过未使用的导入
        if 'from functools import lru_cache' in line and 'performance.py' in file_path:
            continue
        if 'from typing import List' in line and 'test_performance.py' in file_path:
            continue
        if 'SQLiteStorage' in line and '08_turn_lifecycle' in file_path:
            new_line = line.replace('SQLiteStorage,', '').replace(', SQLiteStorage', '')
            new_lines.append(new_line)
        elif 'RecallOptions' in line and '08_turn_lifecycle' in file_path:
            new_line = line.replace('RecallOptions,', '').replace(', RecallOptions', '')
            new_lines.append(new_line)
        else:
            new_lines.append(line)

    with open(file_path, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    print(f"OK Fixed imports: {file_path}")


def fix_f_strings(file_path):
    """修复没有占位符的 f-string"""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 简单的修复：移除不必要的 f 前缀
    patterns = [
        (r'print\(f"(   OK[^{]*?)"\)', r'print("\1")'),
        (r'print\(f"(   ERROR[^{]*?)"\)', r'print("\1")'),
        (r'print\(f"(\nPerformance stats:)"\)', r'print("\1")'),
    ]

    for pattern, replacement in patterns:
        content = re.sub(pattern, replacement, content)

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"OK Fixed f-strings: {file_path}")


def fix_bare_except(file_path):
    """修复裸 except"""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 替换 bare except 为具体异常
    content = re.sub(r'    except:\n        pass  # Already exists',
                     '    except Exception:\n        pass  # Already exists',
                     content)
    content = re.sub(r'    except:\n        pass',
                     '    except Exception:\n        pass',
                     content)

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"OK Fixed bare except: {file_path}")


def fix_unused_variables(file_path):
    """修复未使用的变量"""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 添加 _ 前缀表示故意不使用
    patterns = [
        (r'(\s+)turn = engine\.begin_turn', r'\1_turn = engine.begin_turn'),
        (r'(\s+)turn1 = engine\.begin_turn', r'\1_turn1 = engine.begin_turn'),
        (r'(\s+)turn2 = engine\.begin_turn', r'\1_turn2 = engine.begin_turn'),
        (r'(\s+)receipt = engine\.complete_turn', r'\1_receipt = engine.complete_turn'),
        (r'(\s+)receipt1 = engine\.complete_turn', r'\1_receipt1 = engine.complete_turn'),
        (r'(\s+)receipt2 = engine\.complete_turn', r'\1_receipt2 = engine.complete_turn'),
    ]

    for pattern, replacement in patterns:
        content = re.sub(pattern, replacement, content)

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"OK Fixed unused variables: {file_path}")


if __name__ == '__main__':
    files_to_fix = [
        'erii/server/app.py',
        'erii/performance.py',
        'examples/07_performance_optimization.py',
        'examples/08_turn_lifecycle_integration.py',
        'tests/test_performance.py',
    ]

    for file_path in files_to_fix:
        try:
            print(f"\nFixing {file_path}...")
            fix_unused_exc_variables(file_path)
            fix_unused_imports(file_path)
            fix_f_strings(file_path)
            fix_bare_except(file_path)
            fix_unused_variables(file_path)
        except Exception as e:
            print(f"ERROR Error fixing {file_path}: {e}")

    print("\nOK All fixes applied!")
