import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_fanout_current_owner_authority_is_distinct_from_retired_experiment():
    pack = json.loads((ROOT / "data/model-choice-policy/operating-points.json").read_text())
    entry = next(
        operation
        for route in pack["free_routes"]
        for operation in route["operations"]
        if operation["id"] == "fanout.explore"
    )
    assert entry["basis"] == "owner_authorized"
    assert entry["proof_classes"] == ["judged", "none"]
    assert entry["efforts"] == ["high"]

    README = (ROOT / "data/model-choice-policy/README.md").read_text()
    contract = (ROOT / "tools/model-policy-ops/route-contract.md").read_text()
    for doc in (README, contract):
        normalized = " ".join(doc.split())
        assert "current normal `fanout.explore` free-route authorization" in normalized
        assert "owner_authorized" in doc
        assert "high effort" in doc
        assert "experimental" in doc
        assert "retired" in doc
    assert "were their only authorization" not in README
    assert "was the only path that did" not in contract
