"""Check sampling metadata against the committed benchmark protocol."""
import json
from pathlib import Path
import unittest

PACK = Path(__file__).resolve().parents[1] / "data/decision-model-catalog"


class SampleMetadataTests(unittest.TestCase):
    def test_server_and_basic_rows_describe_their_actual_samples(self):
        rows = json.loads((PACK / "performance/local-decider-cpu-measurements-2026-10-03.json").read_text())["scores"]
        for row in rows:
            with self.subTest(metric=row["metric"]):
                if "stock llama-server" in row["metric"]:
                    first = row["score"] in (2764, 4250)
                    self.assertEqual(row["measurement"].get("n"), 1 if first else 3)
                    self.assertEqual(row["measurement"]["percentile"], "mean" if first else "p50")
                    self.assertIn("n_ctx 8192", row["harness"])
                    self.assertNotIn("median of 5", row["harness"])
                    self.assertNotIn("second request", row["metric"])
                elif "basic" in row["metric"]:
                    self.assertIn("12 distinct cases", row["harness"])
                    self.assertNotIn("Daisy", row["harness"])


if __name__ == "__main__":
    unittest.main()
