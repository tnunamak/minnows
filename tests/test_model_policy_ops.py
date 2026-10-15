import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "model-policy-ops" / "model-policy-ops"


@pytest.fixture(autouse=True)
def isolate_selected_policy(monkeypatch):
    monkeypatch.delenv("DATA_PACKS_HOME", raising=False)


def test_model_policy_ops_check():
    result = subprocess.run([str(TOOL), "check"], cwd=REPO, text=True, capture_output=True)
    assert result.returncode == 0
    assert "PASS model-choice-policy" in result.stdout


def test_model_policy_ops_list_names_standard_op():
    result = subprocess.run([str(TOOL), "list"], cwd=REPO, text=True, capture_output=True)
    assert result.returncode == 0
    assert "implement.standard" in result.stdout
    assert "provider" in result.stdout


def test_model_policy_ops_show_returns_json():
    result = subprocess.run(
        [str(TOOL), "show", "implement.standard"],
        cwd=REPO,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
    assert '"id": "implement.standard"' in result.stdout


@pytest.fixture
def world(tmp_path):
    # Reuse the pinned route world; no live catalog, quota, or destination probes.
    from test_route import World
    return World(tmp_path)


@pytest.fixture
def brief_formatter():
    import runpy
    return runpy.run_path(str(TOOL))["brief_decision"]


def record_flags(decision_id):
    return ["--record", "--decision-id", decision_id, "--parent-model", "pm",
            "--parent-provider-instance", "pp", "--parent-thread", "pt"]


@pytest.mark.parametrize("command", ["route", "resolve"])
def test_brief_is_one_line_and_preserves_exact_target(world, command):
    import json

    extra = [] if command == "route" else ["--account-hint", "claude-work"]
    argv = [command, "implement.quota-tight", *world.args(),
            "--decision-id", "compact", *extra]
    full_run, full = world.cli(*argv)
    result, brief = world.cli(*argv, "--brief")
    assert result.returncode == full_run.returncode == 0, result.stderr
    assert len(result.stdout.splitlines()) == 1
    assert json.loads(result.stdout) == brief
    assert brief["decision_id"] == full["decision_id"]
    assert brief["target"] == full["target"] and brief["target"]["options"] == {"effort": "medium"}
    assert brief["launch_ready"] == full["launch_ready"]
    assert brief["launch_readiness_basis"] == full["launch_readiness_basis"]
    assert brief["selected"]["account"] == full["target"]["providerInstanceId"]
    assert brief["selected"]["arm"] == (
        full["routing"]["final"]["arm"] if command == "route" else full["selection"]["arm"])
    assert brief["selected"]["final_basis"] is None
    assert brief["selected"]["routed_basis"] == (full["routing"]["chosen"]["basis"] if command == "route" else None)
    assert brief["dispatch"]["clientRequestId"] == "compact"
    assert brief["receipt"]["stored"] is False
    assert brief["receipt"]["reason"] == "not_recorded"
    assert "path" not in brief["receipt"]
    assert not world.receipts.exists()


@pytest.mark.parametrize("command", ["route", "resolve"])
def test_brief_keeps_ledger_bytes_and_full_replay_unchanged(world, command):
    import json

    extra = [] if command == "route" else ["--account-hint", "claude-work"]
    argv = [command, "implement.quota-tight", *world.args(), *record_flags("stored"), *extra]
    result, brief = world.cli(*argv, "--brief")
    assert result.returncode == 0, result.stderr
    original = world.receipts.read_bytes()
    full = json.loads(original)
    assert "selection" in full and "selected" not in full and "receipt" not in full
    assert world.receipts.read_bytes() == original
    locator = brief["receipt"]
    assert locator["stored"] is True and locator["format"] == "jsonl"
    rows = [json.loads(line) for line in Path(locator["path"]).read_text().splitlines()]
    stored = next(row for row in rows if row["decision_id"] == locator["decision_id"])
    assert stored["target"] == full["target"] == brief["target"]
    assert "routing" in stored if command == "route" else "selection" in stored
    assert "--brief" in locator["replay"]
    replay_result, replay = world.cli(*argv, "--brief")
    assert replay_result.returncode == 0 and replay["replayed"] is True
    default_result, default = world.cli(*argv)
    json_result, explicit = world.cli(*argv, "--json")
    assert default_result.returncode == json_result.returncode == 0
    assert default_result.stdout == json_result.stdout
    assert default == explicit
    assert {k: v for k, v in explicit.items() if k != "replayed"} == {
        k: v for k, v in full.items() if k != "replayed"}
    assert world.receipts.read_bytes() == original


@pytest.mark.parametrize("command", ["route", "resolve"])
def test_brief_relative_receipt_locator_survives_cwd_change(world, tmp_path, command):
    import json
    import sys

    caller = tmp_path / "caller"
    caller.mkdir()
    world.write()
    extra = [] if command == "route" else ["--account-hint", "claude-work"]
    argv = [str(TOOL), command, "implement.quota-tight", *world.args(),
            "--receipts", "state/decisions.jsonl", *record_flags("relative-locator"),
            *extra, "--brief"]
    result = subprocess.run(argv, cwd=caller, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    locator = json.loads(result.stdout)["receipt"]
    located = Path(locator["path"])
    assert located.is_absolute()
    elsewhere = tmp_path / "reader"
    elsewhere.mkdir()
    reader = subprocess.run(
        [sys.executable, "-c",
         "import json,sys; print(json.loads(open(sys.argv[1]).read())['decision_id'])", str(located)],
        cwd=elsewhere, text=True, capture_output=True,
    )
    assert reader.returncode == 0, reader.stderr
    assert reader.stdout.strip() == "relative-locator"
    assert located == caller / "state/decisions.jsonl"


def test_brief_keeps_all_routed_and_final_judgment_reasons(world):
    argv = ["route", "review.audit", *world.args(), "--decision-id", "override",
            "--override", "effort=medium", "--reason", "bounded review"]
    full_result, full = world.cli(*argv)
    result, brief = world.cli(*argv, "--brief")
    assert full_result.returncode == result.returncode == 0, result.stderr
    assert brief["judgment_reasons"] == full["routing"]["judgment_reasons"]
    assert {row["applies_to"] for row in brief["judgment_reasons"]} == {"routed", "final"}
    assert brief["judgment_required"] == full["routing"]["judgment_required"]
    assert brief["selected"]["arm"] == full["routing"]["final"]["arm"]
    assert brief["target"] == full["target"]
    assert brief["target"]["options"] == {"reasoningEffort": "medium"}
    assert brief["selected"]["final_basis"] is None and brief["selected"]["routed_basis"] is None
    assert brief["routed"]["basis"] == full["routing"]["chosen"]["basis"]


def test_brief_no_eligible_pair_stays_unlaunchable(world):
    world.accounts["accounts"] = {}
    result, brief = world.cli("route", "implement.quota-tight", *world.args(), "--brief")
    assert result.returncode == 0, result.stderr
    assert brief["target"] is None and brief["launch_ready"] is False
    assert brief["dispatch"] is None
    assert brief["judgment_required"] is True
    assert [r["code"] for r in brief["judgment_reasons"]] == ["no_eligible_pair"]


@pytest.mark.parametrize("recorded", [False, True])
def test_brief_simulation_never_dispatches_or_claims_a_stored_simulation(world, recorded):
    from test_route import NOW, non_null_targets

    real = world.route("implement.quota-tight", record="simulation" if recorded else None)
    before = world.receipts.read_bytes() if recorded else None
    result, brief = world.cli("route", "implement.quota-tight", *world.args(),
                              "--decision-id", "simulation", "--now", NOW, "--brief", raw=True)
    assert result.returncode == 0, result.stderr
    assert brief["target"] is None and brief["launch_ready"] is False
    assert non_null_targets(brief) == [] and brief["dispatch"] is None
    assert brief["simulated_target"] == real["target"]
    assert brief["simulation"]["note"] == "simulation only; not a dispatch decision"
    assert brief["receipt"]["stored"] is False and brief["receipt"]["reason"] == "simulation"
    assert "path" not in brief["receipt"]
    assert world.receipts.read_bytes() == before if recorded else not world.receipts.exists()


@pytest.mark.parametrize("extra", [
    ["--override", "account=claude-work"],
    ["--now", "2026-10-10T00:30:00Z", *record_flags("invalid")],
])
def test_brief_preserves_failures_and_exit_status(world, extra):
    argv = ["route", "implement.quota-tight", *world.args(), *extra]
    full, _ = world.cli(*argv, raw=True)
    brief, _ = world.cli(*argv, "--brief", raw=True)
    assert full.returncode == brief.returncode == 1
    assert full.stdout == brief.stdout == ""
    assert full.stderr == brief.stderr
    assert not world.receipts.exists()


@pytest.mark.parametrize("argv", [
    [], ["check"], ["list"], ["show", "implement.standard"], ["audit"],
    ["close"], ["followup"], ["directive", "list"], ["calibrate"], ["observe"],
])
def test_brief_rejects_non_decision_commands(argv):
    result = subprocess.run([str(TOOL), *argv, "--brief"], cwd=REPO, text=True, capture_output=True)
    assert result.returncode == 2
    assert "--brief applies to route and resolve only" in result.stderr
    assert result.stdout == ""


def test_brief_and_json_are_explicitly_mutually_exclusive():
    result = subprocess.run([str(TOOL), "route", "implement.standard", "--brief", "--json"],
                            cwd=REPO, text=True, capture_output=True)
    assert result.returncode == 2 and "--brief and --json are mutually exclusive" in result.stderr


def test_brief_preserves_destination_dispatch_root_identity(world, brief_formatter):
    import copy

    full = world.route("implement.quota-tight")
    full.update({
        "decision_id": "child-relaunch",
        "destination": "dot",
        "target": {"providerInstanceId": "dot", "driverKind": "acpRegistry", "model": "dot"},
        "launch_ready": True,
        "dispatch": {"clientRequestId": "child-relaunch", "mode": "async",
                     "root_decision_id": "original-root", "brief_first_line": "[dot-decision:original-root]"},
        "destination_offer": {"name": "dot", "request_id": "original-root", "purpose": "research",
                              "how_to": "/local/submit", "submit": {"request_id": "original-root"},
                              "capacity": {"available": True, "trace": "not compact"}},
    })
    before = copy.deepcopy(full)
    brief = brief_formatter(full, world.receipts, recorded=True)
    assert brief["target"] == full["target"]
    assert brief["destination"] == "dot" and brief["launch_ready"] is True
    assert brief["selected"] is None and brief["model_fallback"]["selected"]["arm"] == full["routing"]["final"]["arm"]
    assert brief["dispatch"] == full["dispatch"]
    assert brief["dispatch"]["root_decision_id"] != brief["decision_id"]
    assert brief["destination_offer"]["submit"] == full["destination_offer"]["submit"]
    assert "capacity" not in brief["destination_offer"]
    assert full == before


def test_brief_is_bounded_for_huge_trace_and_marks_note_excerpts(world, brief_formatter):
    import copy
    import json

    full = world.route("implement.quota-tight")
    huge = "source-trace-sentinel" * 100000
    full["routing"]["trace"] = [{"trace": huge}] * 100
    full["routing"]["inputs"] = {"catalog": huge, "quota": huge}
    full["availability"] = {"catalog": huge}
    full["routing"]["calibration_notes"] = {
        "applies_changes": False,
        "checked_rejections": [{"arm": {"model": "example"}, "check": "oracle", "rejected": 2}],
        "observations": [{"kind": "observations_stale", "age_hours": 24}],
        "large_note": huge,
    }
    before = copy.deepcopy(full)
    brief = brief_formatter(full, world.receipts, recorded=False)
    text = json.dumps(brief)
    assert len(text) < 10000
    assert "trace" not in brief and "routing" not in brief and "availability" not in brief
    assert brief["calibration_notes"]["checked_rejections"] == full["routing"]["calibration_notes"]["checked_rejections"]
    assert brief["calibration_notes"]["observations"] == full["routing"]["calibration_notes"]["observations"]
    assert brief["calibration_notes"]["large_note"]["truncated"] is True
    assert len(brief["calibration_notes"]["large_note"]["preview"]) <= 512
    assert brief["judgment_reasons"] == full["routing"]["judgment_reasons"]
    assert full == before


def test_brief_real_destination_relaunch_preserves_root_dispatch(world):
    import copy
    import json
    import sys

    provider = copy.deepcopy(world.catalog["data"]["providers"][0])
    provider.update(instanceId="dot", driver="acpRegistry")
    model = copy.deepcopy(provider["models"][0])
    model.update(slug="dot", options=[])
    provider["models"] = [model]
    world.catalog["data"]["providers"].append(provider)
    config = world.tmp / "destinations.json"
    config.write_text(json.dumps({"schema_version": 1, "destinations": [{
        "name": "dot", "purposes": ["research"], "vendor": "codex",
        "timeout_seconds": 5,
        "capacity_command": [sys.executable, "-c", 'print(\'{"available":true,"reason":"fixture"}\')'],
        "how_to": "/local/submit", "submit": {"request_id": "<request_id>"},
        "target": {"providerInstanceId": "dot", "driverKind": "acpRegistry", "model": "dot"},
        "brief_token": "[dot-decision:<root_decision_id>]", "max_chain_failures": 2,
    }]}))
    extra = ["--destinations", config, "--purpose", "research", "--inputs", "github"]
    root = world.route("implement.quota-tight", *extra, record="dot-root-original")
    assert root["destination"] == "dot" and root["launch_ready"] is True
    argv = ["route", "implement.quota-tight", *world.args(), *extra,
            *record_flags("dot-child-relaunch"), "--relaunch-of", "dot-root-original",
            "--why", "infra", "--reason", "fixture relaunch"]
    result, brief = world.cli(*argv, "--brief")
    assert result.returncode == 0, result.stderr
    replay_result, full = world.cli(*argv, "--json")
    assert replay_result.returncode == 0, replay_result.stderr
    assert brief["target"] == full["target"] == root["target"]
    assert brief["dispatch"] == full["dispatch"]
    assert brief["dispatch"]["root_decision_id"] == "dot-root-original"
    assert brief["dispatch"]["brief_first_line"] == "[dot-decision:dot-root-original]"
    assert brief["decision_id"] == "dot-child-relaunch" and brief["selected"] is None
