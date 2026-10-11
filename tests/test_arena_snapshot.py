import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads((ROOT / path).read_text())


class CatalogMetadataTest(unittest.TestCase):
    def test_metadata_contract(self):
        a = load("data/model-catalog/performance/arena-text-2026-10-07.json")["scores"]
        b = load("data/model-catalog/performance/third-party-board-roster-refresh-2026-10-07.json")["scores"]
        overall = [r for r in a + b if r["source_id"] in ("arena-text-leaderboard-2026-10-07", "arena-ai-text-haiku-5-5-2026-10-07")]
        coding = [r for r in a if r["source_id"] == "arena-text-coding-leaderboard-2026-10-07"]
        self.assertEqual(len(overall), 30)
        self.assertEqual({r["snapshot_id"] for r in overall}, {"arena-text-overall-2026-10-02"})
        self.assertEqual({r["comparability_group"] for r in overall}, {"arena-text-overall-2026-10-02"})
        self.assertEqual({r["snapshot_id"] for r in coding}, {"arena-text-coding-2026-10-07"})
        sources = load("data/model-catalog/SOURCES.json")["sources"]
        source = next(s for s in sources if s["id"] == "arena-ai-text-haiku-5-5-2026-10-07")
        self.assertIn("dated Oct 2, 2026", source["notes"])


if __name__ == "__main__":
    unittest.main()
