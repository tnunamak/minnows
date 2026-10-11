import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "demo_lane_trace.sh"


class DemoLaneTraceTests(unittest.TestCase):
    def test_catalog_reference_without_source_provenance_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            catalog = root / "data" / "model-catalog"
            (catalog / "performance").mkdir(parents=True)
            (catalog / "performance" / "model.json").write_text("{}", encoding="utf-8")
            (catalog / "SOURCES.json").write_text('{"sources": []}\n', encoding="utf-8")
            policy = root / "policy.json"
            policy.write_text(json.dumps({
                "operating_points": [{
                    "id": "test-op",
                    "evidence_refs": ["catalog://performance/model"],
                }],
            }), encoding="utf-8")
            env = os.environ | {
                "MINNOWS_ROOT": str(root),
                "MODEL_CHOICE_POLICY": str(policy),
            }
            result = subprocess.run(
                ["bash", str(SCRIPT), "test-op"],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("no source provenance resolved", result.stderr)


if __name__ == "__main__":
    unittest.main()
