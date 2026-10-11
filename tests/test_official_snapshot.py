import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads((ROOT / path).read_text())


class CatalogMetadataTest(unittest.TestCase):
    def test_metadata_contract(self):
        a = load("data/model-catalog/performance/tbench-ai-official-2026-10-07.json")["scores"]
        b = load("data/model-catalog/performance/third-party-board-roster-refresh-2026-10-07.json")["scores"]
        rows = [r for r in a + b if r.get("comparability_group") == "terminal-bench-4-0-tbench-ai-official-2026-10-07"]
        self.assertEqual(len(rows), 23)
        self.assertEqual({r["snapshot_id"] for r in rows}, {"tbench-ai-terminal-bench-4-0-2026-10-07"})


if __name__ == "__main__":
    unittest.main()
