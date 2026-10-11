import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads((ROOT / path).read_text())


class CatalogMetadataTest(unittest.TestCase):
    def test_metadata_contract(self):
        doc = (ROOT / "data/decision-model-catalog/README.md").read_text()
        # Three separate requests each repeat the state; a batched request shares it.
        narrative = re.search(r"roughly ([0-9.]+)x the per-decision", doc)
        self.assertIsNotNone(narrative)
        actual = 2100 / (2300 / 3)
        self.assertAlmostEqual(float(narrative.group(1)), actual, places=2)


if __name__ == "__main__":
    unittest.main()
