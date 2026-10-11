import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PricingMatchTest(unittest.TestCase):
    def test_specific_models_and_family_fallback(self):
        doc = json.loads((ROOT / 'data/decision-model-catalog/pricing/self-hosted-open-weights-2026-10.json').read_text())
        for identifier, expected in [('clef-flash', 'clef-flash'), ('clef', 'clef')]:
            with self.subTest(identifier=identifier):
                # The pricing contract is ordered, first-substring matching.
                actual = next(rule["model"] for rule in doc["match"]
                              if rule["pattern"] in identifier)
                self.assertEqual(actual, expected)
                self.assertIn(actual, doc["models"])


if __name__ == "__main__":
    unittest.main()
