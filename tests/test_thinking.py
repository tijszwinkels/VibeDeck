"""Tests for thinking level detection."""

import pytest

from vibedeck.backends.thinking import (
    LEVELS,
    detect_thinking_level,
    get_thinking_env,
)


class TestDetectThinkingLevel:
    """Tests for detect_thinking_level function."""

    # Default level tests (no keywords)
    def test_no_keywords_returns_default(self):
        """Messages without thinking keywords get default level."""
        result = detect_thinking_level("Hello, how are you?")
        assert result.name == "default"
        assert result.budget_tokens == 2048

    def test_empty_message_returns_default(self):
        """Empty message gets default level."""
        result = detect_thinking_level("")
        assert result.name == "default"

    def test_unrelated_think_word_returns_low(self):
        """The word 'think' alone triggers low level."""
        result = detect_thinking_level("I think this is interesting")
        assert result.name == "low"
        assert result.budget_tokens == 4000

    # Low level tests (4000 tokens)
    def test_think_keyword(self):
        """'think' keyword triggers low level."""
        result = detect_thinking_level("think about this problem")
        assert result.name == "low"
        assert result.budget_tokens == 4000

    def test_think_case_insensitive(self):
        """Keywords are case insensitive."""
        result = detect_thinking_level("THINK about this")
        assert result.name == "low"

    # Medium level tests (10000 tokens)
    def test_think_hard(self):
        """'think hard' triggers medium level."""
        result = detect_thinking_level("think hard about this")
        assert result.name == "medium"
        assert result.budget_tokens == 10000

    def test_think_deeply(self):
        """'think deeply' triggers medium level."""
        result = detect_thinking_level("please think deeply")
        assert result.name == "medium"

    def test_think_more(self):
        """'think more' triggers medium level."""
        result = detect_thinking_level("think more about the edge cases")
        assert result.name == "medium"

    def test_think_about_it(self):
        """'think about it' triggers medium level."""
        result = detect_thinking_level("think about it carefully")
        assert result.name == "medium"

    def test_think_a_lot(self):
        """'think a lot' triggers medium level."""
        result = detect_thinking_level("think a lot before answering")
        assert result.name == "medium"

    def test_megathink(self):
        """'megathink' triggers medium level."""
        result = detect_thinking_level("megathink: solve this")
        assert result.name == "medium"

    def test_megathink_uppercase(self):
        """'MEGATHINK' is case insensitive."""
        result = detect_thinking_level("MEGATHINK please")
        assert result.name == "medium"

    # Max level tests (31999 tokens)
    def test_ultrathink(self):
        """'ultrathink' triggers max level."""
        result = detect_thinking_level("ultrathink: complex problem")
        assert result.name == "max"
        assert result.budget_tokens == 31999

    def test_think_harder(self):
        """'think harder' triggers max level."""
        result = detect_thinking_level("think harder about this")
        assert result.name == "max"

    def test_think_intensely(self):
        """'think intensely' triggers max level."""
        result = detect_thinking_level("think intensely")
        assert result.name == "max"

    def test_think_longer(self):
        """'think longer' triggers max level."""
        result = detect_thinking_level("think longer on this")
        assert result.name == "max"

    def test_think_really_hard(self):
        """'think really hard' triggers max level."""
        result = detect_thinking_level("think really hard")
        assert result.name == "max"

    def test_think_super_hard(self):
        """'think super hard' triggers max level."""
        result = detect_thinking_level("think super hard")
        assert result.name == "max"

    def test_think_very_hard(self):
        """'think very hard' triggers max level."""
        result = detect_thinking_level("think very hard")
        assert result.name == "max"

    # Priority tests (higher levels take precedence)
    def test_max_takes_precedence_over_medium(self):
        """Max level patterns beat medium level patterns."""
        # "think harder" should win over "think hard"
        result = detect_thinking_level("think harder about this")
        assert result.name == "max"

    def test_medium_takes_precedence_over_low(self):
        """Medium level patterns beat low level patterns."""
        # "think hard" should win over just "think"
        result = detect_thinking_level("think hard about this")
        assert result.name == "medium"

    def test_multiple_keywords_highest_wins(self):
        """When multiple keywords present, highest level wins."""
        result = detect_thinking_level("ultrathink and think hard about this")
        assert result.name == "max"

    # Edge cases
    def test_think_in_middle_of_word_not_matched(self):
        """'think' embedded in another word is not matched."""
        result = detect_thinking_level("rethinking the approach")
        assert result.name == "default"

    def test_think_with_punctuation(self):
        """'think' followed by punctuation is matched."""
        result = detect_thinking_level("think, then act")
        assert result.name == "low"

    def test_think_at_end_of_sentence(self):
        """'think' at end of sentence is matched."""
        result = detect_thinking_level("let me think")
        assert result.name == "low"

    def test_whitespace_variations(self):
        """Patterns with multiple spaces still match."""
        # The regex uses \s+ so multiple spaces should work
        result = detect_thinking_level("think   hard")
        assert result.name == "medium"


class TestGetThinkingEnv:
    """Tests for get_thinking_env function."""

    def test_returns_dict_with_max_thinking_tokens(self):
        """Returns dict with MAX_THINKING_TOKENS key."""
        env = get_thinking_env("hello")
        assert "MAX_THINKING_TOKENS" in env

    def test_default_level_value(self):
        """Default level returns 2048."""
        env = get_thinking_env("hello")
        assert env["MAX_THINKING_TOKENS"] == "2048"

    def test_low_level_value(self):
        """Low level returns 4000."""
        env = get_thinking_env("think about this")
        assert env["MAX_THINKING_TOKENS"] == "4000"

    def test_medium_level_value(self):
        """Medium level returns 10000."""
        env = get_thinking_env("think hard")
        assert env["MAX_THINKING_TOKENS"] == "10000"

    def test_max_level_value(self):
        """Max level returns 31999."""
        env = get_thinking_env("ultrathink")
        assert env["MAX_THINKING_TOKENS"] == "31999"

    def test_value_is_string(self):
        """Environment variable value is a string."""
        env = get_thinking_env("ultrathink")
        assert isinstance(env["MAX_THINKING_TOKENS"], str)


class TestLevelsConstant:
    """Tests for the LEVELS constant."""

    def test_all_levels_exist(self):
        """All expected levels are defined."""
        assert "default" in LEVELS
        assert "low" in LEVELS
        assert "medium" in LEVELS
        assert "max" in LEVELS

    def test_default_budget(self):
        """Default level has correct budget."""
        assert LEVELS["default"].budget_tokens == 2048

    def test_low_budget(self):
        """Low level has correct budget."""
        assert LEVELS["low"].budget_tokens == 4000

    def test_medium_budget(self):
        """Medium level has correct budget."""
        assert LEVELS["medium"].budget_tokens == 10000

    def test_max_budget(self):
        """Max level has correct budget."""
        assert LEVELS["max"].budget_tokens == 31999
