"""Test span calculator with Unicode, emoji, and duplicate quotes."""

import sys
sys.path.insert(0, 'D:/bate/erii')
sys.path.insert(0, 'D:/bate/erii/experiments/deepseek-continuity-review/src')

from erii_deepseek_continuity.span_calculator import (
    calculate_span,
    SpanCalculationError,
)


def test_simple_span():
    """Test basic span calculation."""
    print("Test: Simple span calculation...")

    result = calculate_span(
        proposed_reply="Hello world",
        reply_quote="world",
        occurrence=0,
    )

    assert result.reply_start == 6
    assert result.reply_end == 11
    assert result.reply_quote == "world"
    print("OK")


def test_unicode_chinese():
    """Test Unicode Chinese characters."""
    print("Test: Unicode Chinese...")

    result = calculate_span(
        proposed_reply="你好世界，今天天气不错",
        reply_quote="天气",
        occurrence=0,
    )

    assert result.reply_start == 7
    assert result.reply_end == 9
    assert result.reply_quote == "天气"
    print("OK")


def test_emoji():
    """Test emoji span calculation."""
    print("Test: Emoji...")

    reply = "I love coding 💻 and coffee ☕"
    result = calculate_span(
        proposed_reply=reply,
        reply_quote="💻",
        occurrence=0,
    )

    assert result.reply_quote == "💻"
    assert reply[result.reply_start:result.reply_end] == "💻"
    print("OK")


def test_duplicate_quote_first_occurrence():
    """Test duplicate quote with first occurrence."""
    print("Test: Duplicate quote (first)...")

    result = calculate_span(
        proposed_reply="hello world hello universe",
        reply_quote="hello",
        occurrence=0,
    )

    assert result.reply_start == 0
    assert result.reply_end == 5
    print("OK")


def test_duplicate_quote_second_occurrence():
    """Test duplicate quote with second occurrence."""
    print("Test: Duplicate quote (second)...")

    result = calculate_span(
        proposed_reply="hello world hello universe",
        reply_quote="hello",
        occurrence=1,
    )

    assert result.reply_start == 12
    assert result.reply_end == 17
    print("OK")


def test_duplicate_without_occurrence_fails():
    """Test that duplicate without occurrence fails."""
    print("Test: Duplicate without occurrence fails...")

    try:
        calculate_span(
            proposed_reply="hello world hello universe",
            reply_quote="hello",
            occurrence=None,
        )
        assert False, "Should have raised SpanCalculationError"
    except SpanCalculationError as e:
        assert "appears 2 times" in str(e)
    print("OK")


def test_quote_not_found_fails():
    """Test that quote not found fails."""
    print("Test: Quote not found fails...")

    try:
        calculate_span(
            proposed_reply="hello world",
            reply_quote="goodbye",
            occurrence=0,
        )
        assert False, "Should have raised SpanCalculationError"
    except SpanCalculationError as e:
        assert "not found" in str(e)
    print("OK")


def test_occurrence_out_of_range_fails():
    """Test that occurrence out of range fails."""
    print("Test: Occurrence out of range fails...")

    try:
        calculate_span(
            proposed_reply="hello world",
            reply_quote="hello",
            occurrence=5,
        )
        assert False, "Should have raised SpanCalculationError"
    except SpanCalculationError as e:
        assert "out of range" in str(e)
    print("OK")


def test_empty_quote_fails():
    """Test that empty quote fails."""
    print("Test: Empty quote fails...")

    try:
        calculate_span(
            proposed_reply="hello world",
            reply_quote="",
            occurrence=0,
        )
        assert False, "Should have raised SpanCalculationError"
    except SpanCalculationError as e:
        assert "cannot be empty" in str(e)
    print("OK")


def test_mixed_unicode_and_emoji():
    """Test mixed Unicode and emoji."""
    print("Test: Mixed Unicode and emoji...")

    reply = "绘梨衣很开心 😊 今天天气很好"
    result = calculate_span(
        proposed_reply=reply,
        reply_quote="😊",
        occurrence=0,
    )

    assert result.reply_quote == "😊"
    assert reply[result.reply_start:result.reply_end] == "😊"
    print("OK")


def test_repeated_chinese_character():
    """Test repeated Chinese character."""
    print("Test: Repeated Chinese character...")

    # "天" appears twice
    result = calculate_span(
        proposed_reply="今天天气很好",
        reply_quote="天",
        occurrence=1,  # Second occurrence
    )

    assert result.reply_start == 2
    assert result.reply_end == 3
    assert result.reply_quote == "天"
    print("OK")


if __name__ == "__main__":
    tests = [
        test_simple_span,
        test_unicode_chinese,
        test_emoji,
        test_duplicate_quote_first_occurrence,
        test_duplicate_quote_second_occurrence,
        test_duplicate_without_occurrence_fails,
        test_quote_not_found_fails,
        test_occurrence_out_of_range_fails,
        test_empty_quote_fails,
        test_mixed_unicode_and_emoji,
        test_repeated_chinese_character,
    ]

    failed = 0
    for test in tests:
        try:
            test()
        except Exception as e:
            print(f"FAILED: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    if failed > 0:
        sys.exit(1)
