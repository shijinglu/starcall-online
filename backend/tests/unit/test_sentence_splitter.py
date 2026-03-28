"""Unit tests for the sentence boundary splitter."""

from app.deep_agent_runner import split_into_sentences


class TestSplitIntoSentences:
    def test_single_sentence(self):
        result = list(split_into_sentences("Hello world."))
        assert result == ["Hello world."]

    def test_two_sentences(self):
        result = list(split_into_sentences("First sentence. Second sentence."))
        assert result == ["First sentence.", "Second sentence."]

    def test_question_mark(self):
        result = list(split_into_sentences("What is this? I don't know."))
        assert result == ["What is this?", "I don't know."]

    def test_exclamation_mark(self):
        result = list(split_into_sentences("Wow! That is great."))
        assert result == ["Wow!", "That is great."]

    def test_abbreviation_dr(self):
        result = list(split_into_sentences("Dr. Smith is here. He is ready."))
        assert result == ["Dr. Smith is here.", "He is ready."]

    def test_abbreviation_mr(self):
        result = list(split_into_sentences("Mr. Jones arrived. He sat down."))
        assert result == ["Mr. Jones arrived.", "He sat down."]

    def test_abbreviation_etc(self):
        result = list(split_into_sentences("Items etc. are listed. Check them."))
        assert result == ["Items etc. are listed.", "Check them."]

    def test_abbreviation_vs(self):
        result = list(split_into_sentences("Apple vs. Google compete. Both are big."))
        assert result == ["Apple vs. Google compete.", "Both are big."]

    def test_no_trailing_punctuation(self):
        result = list(split_into_sentences("No period at the end"))
        assert result == ["No period at the end"]

    def test_empty_string(self):
        result = list(split_into_sentences(""))
        assert result == []

    def test_whitespace_only(self):
        result = list(split_into_sentences("   "))
        assert result == []

    def test_multiple_spaces_between_sentences(self):
        result = list(split_into_sentences("First.   Second."))
        assert result == ["First.", "Second."]

    def test_mixed_punctuation(self):
        result = list(split_into_sentences("Really? Yes! OK."))
        assert result == ["Really?", "Yes!", "OK."]
