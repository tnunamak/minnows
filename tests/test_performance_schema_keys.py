import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads((ROOT / path).read_text())


class CatalogMetadataTest(unittest.TestCase):
    def test_metadata_contract(self):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"Duplicate schema key: {key}")
                result[key] = value
            return result
        schema = json.loads((ROOT / "data/model-catalog/schemas/performance-v1.schema.json").read_text(), object_pairs_hook=unique)
        props = schema["properties"]["scores"]["items"]["properties"]
        self.assertEqual(props["n"]["minimum"], 1)
        self.assertIn("95%", props["ci_lo"]["description"])
        self.assertIn("95%", props["ci_hi"]["description"])


if __name__ == "__main__":
    unittest.main()
