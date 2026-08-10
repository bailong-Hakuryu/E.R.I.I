#!/usr/bin/env python3
"""
E.R.I.I. v0.5.0a2 综合测试套件

测试所有核心功能、新增功能和关键路径。
"""

import sys
import unittest
import time
from io import StringIO


class TestRunner:
    """综合测试运行器"""

    def __init__(self):
        self.results = {
            "passed": [],
            "failed": [],
            "errors": [],
            "skipped": []
        }

    def run_test_suite(self, name, pattern):
        """运行测试套件"""
        print(f"\n{'='*70}")
        print(f"运行测试套件: {name}")
        print(f"{'='*70}")

        loader = unittest.TestLoader()
        suite = loader.discover("tests", pattern=pattern)

        # 捕获输出
        stream = StringIO()
        runner = unittest.TextTestRunner(stream=stream, verbosity=2)

        start_time = time.time()
        result = runner.run(suite)
        duration = time.time() - start_time

        # 统计结果
        passed = result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)

        print(f"\n测试结果:")
        print(f"  总计: {result.testsRun}")
        print(f"  通过: {passed}")
        print(f"  失败: {len(result.failures)}")
        print(f"  错误: {len(result.errors)}")
        print(f"  跳过: {len(result.skipped)}")
        print(f"  耗时: {duration:.2f}s")

        if result.failures:
            print(f"\n失败的测试:")
            for test, traceback in result.failures:
                print(f"  - {test}")

        if result.errors:
            print(f"\n错误的测试:")
            for test, traceback in result.errors:
                print(f"  - {test}")

        self.results["passed"].append((name, passed))
        self.results["failed"].append((name, len(result.failures)))
        self.results["errors"].append((name, len(result.errors)))
        self.results["skipped"].append((name, len(result.skipped)))

        return result.wasSuccessful()

    def print_summary(self):
        """打印总结"""
        print(f"\n{'='*70}")
        print(f"综合测试总结")
        print(f"{'='*70}")

        total_passed = sum(count for _, count in self.results["passed"])
        total_failed = sum(count for _, count in self.results["failed"])
        total_errors = sum(count for _, count in self.results["errors"])
        total_skipped = sum(count for _, count in self.results["skipped"])
        total_tests = total_passed + total_failed + total_errors

        print(f"\n总体统计:")
        print(f"  总测试数: {total_tests}")
        print(f"  通过: {total_passed} ({total_passed/total_tests*100:.1f}%)")
        print(f"  失败: {total_failed}")
        print(f"  错误: {total_errors}")
        print(f"  跳过: {total_skipped}")

        if total_failed == 0 and total_errors == 0:
            print(f"\n✅ 所有核心测试通过！")
            return True
        else:
            print(f"\n❌ 有测试失败，需要检查")
            return False


def main():
    """主测试流程"""
    print("="*70)
    print("E.R.I.I. v0.5.0a2 综合测试")
    print("="*70)

    runner = TestRunner()
    all_passed = True

    # 1. 核心功能测试
    print("\n[1/7] 核心引擎测试...")
    passed = runner.run_test_suite(
        "核心引擎",
        "test_engine.py"
    )
    all_passed = all_passed and passed

    # 2. Turn 生命周期测试
    print("\n[2/7] Turn 生命周期测试...")
    passed = runner.run_test_suite(
        "Turn 生命周期",
        "test_turn_lifecycle_public.py"
    )
    all_passed = all_passed and passed

    # 3. 性能优化测试
    print("\n[3/7] 性能优化测试...")
    passed = runner.run_test_suite(
        "性能优化",
        "test_performance_optimization.py"
    )
    all_passed = all_passed and passed

    # 4. 性能基线测试
    print("\n[4/7] 性能基线测试...")
    passed = runner.run_test_suite(
        "性能基线",
        "test_performance.py"
    )
    all_passed = all_passed and passed

    # 5. 并发测试
    print("\n[5/7] 并发测试...")
    passed = runner.run_test_suite(
        "并发安全",
        "test_concurrency.py"
    )
    all_passed = all_passed and passed

    # 6. 安全测试
    print("\n[6/7] 安全测试...")
    passed = runner.run_test_suite(
        "安全功能",
        "test_prompt_injection_security.py"
    )
    all_passed = all_passed and passed

    # 7. 存储测试
    print("\n[7/7] 存储层测试...")
    passed = runner.run_test_suite(
        "存储层",
        "test_storage*.py"
    )
    all_passed = all_passed and passed

    # 打印总结
    success = runner.print_summary()

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
