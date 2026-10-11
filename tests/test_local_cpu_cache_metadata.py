"""Keep recorded cache conditions consistent with raw latency evidence."""
import json
from pathlib import Path
import unittest

PACK = Path(__file__).resolve().parents[1] / "data/decision-model-catalog"


class CacheMetadataTests(unittest.TestCase):
    def test_repeated_identical_request_is_marked_cached(self):
        rows = json.loads((PACK / "performance/local-decider-cpu-measurements-2026-10-03.json").read_text())["scores"]
        raw = json.loads((PACK / "evidence/local-decider-cpu-bench/r_2b_q8_server.json").read_text())
        row, = [row for row in rows if "repeated identical request" in row["metric"]]
        self.assertEqual(row["score"], raw["single_choice12"]["warm_median_ms"])
        self.assertLess(sum(raw["single_choice12"]["prompt_tokens_processed_warm"]), sum(raw["single_choice12"]["prompt_tokens_processed_first"]))
        self.assertIs(row["measurement"]["prefix_cache"], True)


if __name__ == "__main__":
    unittest.main()
