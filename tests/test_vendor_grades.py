import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads((ROOT / path).read_text())


class CatalogMetadataTest(unittest.TestCase):
    def test_metadata_contract(self):
        rows = load("data/model-catalog/performance/anthropic-opus-5-5-system-card-2026-09.json")["scores"]
        vendor = [r for r in rows if r.get("source_type") == "vendor_table"]
        self.assertTrue(vendor)
        for row in vendor:
            with self.subTest(metric=row["metric"]):
                self.assertIn(row["evidence_grade"], ("C", "D"))


if __name__ == "__main__":
    unittest.main()
