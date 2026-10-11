import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PricingMatchTest(unittest.TestCase):
    def test_specific_models_and_family_fallback(self):
        doc = json.loads((ROOT / 'data/model-catalog/pricing/anthropic-api-2026-07.json').read_text())
        for identifier, expected in [('claude-fable-5-1', 'claude-fable-5-1'), ('claude-mythos-5-1', 'claude-mythos-5-1'), ('claude-fable-5', 'claude-fable-5'), ('claude-mythos-5', 'claude-mythos-5'), ('claude-fable-unknown', 'claude-fable-5')]:
            with self.subTest(identifier=identifier):
                # The pricing contract is ordered, first-substring matching.
                actual = next(rule["model"] for rule in doc["match"]
                              if rule["pattern"] in identifier)
                self.assertEqual(actual, expected)
                self.assertIn(actual, doc["models"])


if __name__ == "__main__":
    unittest.main()
