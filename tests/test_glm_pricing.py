import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PricingMatchTest(unittest.TestCase):
    def test_specific_models_and_family_fallback(self):
        doc = json.loads((ROOT / 'data/model-catalog/pricing/openrouter-openweight-frontier-2026-09-23.json').read_text())
        for identifier, expected in [('z-ai/glm-5.3-flash', 'glm-5.3-flash'), ('z-ai/glm-5.3', 'glm-5.3')]:
            with self.subTest(identifier=identifier):
                # The pricing contract is ordered, first-substring matching.
                actual = next(rule["model"] for rule in doc["match"]
                              if rule["pattern"] in identifier)
                self.assertEqual(actual, expected)
                self.assertIn(actual, doc["models"])


if __name__ == "__main__":
    unittest.main()
