import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads((ROOT / path).read_text())


class CatalogMetadataTest(unittest.TestCase):
    def test_metadata_contract(self):
        doc = load("data/decision-model-catalog/performance/requesty-decision-benchmark-2026-10-05.json")
        rows = [r for r in doc["scores"] if r.get("n") == 62]
        self.assertEqual(len(rows), 3)
        for row in rows:
            with self.subTest(metric=row["metric_id"]):
                self.assertFalse(row.get("comparable", True))


if __name__ == "__main__":
    unittest.main()
