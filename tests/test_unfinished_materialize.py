import sys
import unittest
from pathlib import Path


LIB = Path(__file__).parents[1] / "tools" / "unfinished" / "lib"
sys.path.insert(0, str(LIB))
import materialize


class QuoteSpanTests(unittest.TestCase):
    def test_reordered_words_do_not_count_as_a_source_quote(self):
        self.assertFalse(materialize.quote_ok(
            "source the exact phrase belongs here",
            "the exact source phrase belongs here",
        ))

    def test_exact_quote_span_is_accepted(self):
        self.assertTrue(materialize.quote_ok(
            "the exact source phrase",
            "prefix text: the exact source phrase, followed by more text",
        ))

    def test_ellipsis_allows_multiple_verbatim_source_spans(self):
        self.assertTrue(materialize.quote_ok(
            "the first source phrase ... the later source phrase",
            "the first source phrase appears before unrelated text and the later source phrase",
        ))

    def test_ellipsis_spans_must_keep_source_order(self):
        self.assertFalse(materialize.quote_ok(
            "the later source phrase ... the first source phrase",
            "the first source phrase appears before unrelated text and the later source phrase",
        ))

    def test_empty_or_missing_spans_are_rejected(self):
        self.assertFalse(materialize.quote_ok(" ... ", "any source text"))
        self.assertFalse(materialize.quote_ok("fabricated source words", "different text entirely"))


if __name__ == "__main__":
    unittest.main()
