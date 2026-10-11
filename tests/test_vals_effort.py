import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads((ROOT / path).read_text())


class CatalogMetadataTest(unittest.TestCase):
    def test_metadata_contract(self):
        doc = load("data/model-catalog/performance/third-party-board-roster-refresh-2026-10-07.json")
        rows = [r for r in doc["scores"] if r["comparability_group"] == "vals-ai-terminal-bench-4-2026-10-06"]
        self.assertEqual(len(rows), 6)
        for row in rows:
            with self.subTest(model=row["model"]):
                self.assertEqual(row["effort"], "max")
                self.assertIn("board JSON payload records max", row["caveat"])
        provenance = load("data/model-catalog/performance/vals-ai-terminal-bench-4-2026-10-06.json")["notes"]
        self.assertIn("max for all six", provenance)
        self.assertNotIn("records their effort as 'unknown'", provenance)


if __name__ == "__main__":
    unittest.main()
