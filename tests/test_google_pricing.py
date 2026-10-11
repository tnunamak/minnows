import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PricingMatchTest(unittest.TestCase):
    def test_specific_models_and_family_fallback(self):
        doc = json.loads((ROOT / 'data/model-catalog/pricing/google-api-2026-07.json').read_text())
        for identifier, expected in [('gemini-3.8-flash', 'gemini-3.8-flash'), ('gemini-3.7-flash', 'gemini-3.7-flash'), ('gemini-3.6-flash', 'gemini-3.6-flash'), ('gemini-3.5-flash-lite', 'gemini-3.5-flash-lite'), ('gemini-3.5-flash', 'gemini-3.5-flash'), ('gemini-unknown', 'gemini-3.1-pro-preview')]:
            with self.subTest(identifier=identifier):
                # The pricing contract is ordered, first-substring matching.
                actual = next(rule["model"] for rule in doc["match"]
                              if rule["pattern"] in identifier)
                self.assertEqual(actual, expected)
                self.assertIn(actual, doc["models"])


if __name__ == "__main__":
    unittest.main()
