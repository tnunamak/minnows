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


class Gemini4ArgonPricingTest(unittest.TestCase):
    def test_argon_ids_resolve_before_the_family_default_and_carry_the_intro_window(self):
        doc = json.loads((ROOT / 'data/model-catalog/pricing/google-api-2026-07.json').read_text())
        def resolve(identifier):
            return next(rule["model"] for rule in doc["match"] if rule["pattern"] in identifier)
        # Routed and vendor-prefixed ids must not fall through to the catch-all gemini rule.
        for identifier in ('gemini-4-argon', 'google/gemini-4-argon', 'google/gemini-4-argon-20261013',
                           'models/gemini-4-argon', 'publishers/google/models/gemini-4-argon',
                           'gemini-4-argon-high', 'gemini-4-argon:batch'):
            with self.subTest(identifier=identifier):
                self.assertEqual(resolve(identifier), 'gemini-4-argon')
        # Older ids and unrelated Gemini 4 siblings must not be priced at Argon rates.
        for identifier in ('gemini-4-flash', 'gemini-3.8-flash', 'gemini-3.1-pro-preview'):
            with self.subTest(identifier=identifier):
                self.assertNotEqual(resolve(identifier), 'gemini-4-argon')
        row = doc["models"]["gemini-4-argon"]
        self.assertEqual((row["fresh_input_per_m"], row["cache_read_per_m"], row["output_per_m"]), (2.0, 0.1, 10.0))
        self.assertEqual(row["valid_until"], "2026-11-30")
        self.assertEqual(row["post_valid_until"]["fresh_input_per_m"], 4.0)
        self.assertEqual(row["post_valid_until"]["output_per_m"], 20.0)


if __name__ == "__main__":
    unittest.main()
