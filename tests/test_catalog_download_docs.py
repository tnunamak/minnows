import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads((ROOT / path).read_text())


class CatalogMetadataTest(unittest.TestCase):
    def test_metadata_contract(self):
        doc = (ROOT / "data/model-catalog/README.md").read_text()
        tag = load("data/model-catalog/pack.json")["tag"]
        section = doc.split("### Full pack", 1)[1].split("```", 2)[1]
        self.assertIn(f"TAG={tag}\n", section)
        version = tag.removeprefix("data-model-catalog-")
        self.assertIn(f"fetch-data-pack.sh model-catalog {version}\n", section)


if __name__ == "__main__":
    unittest.main()
