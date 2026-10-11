import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads((ROOT / path).read_text())


class CatalogMetadataTest(unittest.TestCase):
    def test_metadata_contract(self):
        doc = (ROOT / "data/model-choice-policy/README.md").read_text()
        tag = load("data/model-choice-policy/pack.json")["tag"]
        self.assertIn(f"TAG={tag}\n", doc)
        current = next(line for line in doc.splitlines() if "**This version**" in line)
        self.assertIn(f"[{tag}]", current)
        self.assertIn(f"/tag/{tag})", current)


if __name__ == "__main__":
    unittest.main()
