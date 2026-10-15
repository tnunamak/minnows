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




# These fixtures are synthetic caller judgments, not claims that a real task was accepted.
import argparse
import copy
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(REPO / "tools" / "model-policy-ops" / "lib"))
import decision_receipts as receipts
import outcomes


def task_link(task="t1", stage="make", role="maker", workflow="w1"):
    return {"v": 1, "consumer_task_id": task, "workflow_id": workflow, "stage_id": stage, "role": role}


def task_result(task="t1", supersedes=None):
    return {"v": 1, "consumer_task_id": task, "supersedes_task_close_id": supersedes, "standard_evidence": [0]}


@pytest.fixture
def ledger(tmp_path):
    tmp_path.chmod(0o700)
    standard = tmp_path / "standard.txt"
    standard.write_text("Synthetic acceptance-standard fixture. Each close is a caller claim.\n")
    evidence = {"type": "file", "path": str(standard),
                "sha256": hashlib.sha256(standard.read_bytes()).hexdigest()}
    return tmp_path / "receipts.jsonl", tmp_path / "outcomes.jsonl", evidence


def put_receipt(ledger, decision, link=None):
    value = {"schema_version": 1, "decision_id": decision, "request": {"fixture": decision}}
    if link is not None:
        value["task_link"] = link
    return receipts.append_receipt(ledger[0], value)


def close_args(ledger, decision, close_id, result=None, supersedes=None, outcome="accepted", repairs=None):
    return argparse.Namespace(
        receipts=ledger[0], outcomes=ledger[1], op=decision, close_id=close_id,
        outcome=outcome, judged_by="parent", check="independent_judged",
        owner_input=None, repairs=repairs, evidence=[json.dumps(ledger[2])],
        note=None, reason="Synthetic correction" if supersedes else None,
        closer_thread=None, supersedes=supersedes,
        task_result=json.dumps(result) if result is not None else None)


def close(ledger, decision, close_id, **kwargs):
    return outcomes.write_close(close_args(ledger, decision, close_id, **kwargs),
                                receipts.find_receipt(ledger[0], decision))


def project(ledger):
    return outcomes.task_projection(receipts.read_receipts(ledger[0]), outcomes.read_outcomes(ledger[1]))


def test_task_maker_checker_repair_counts_only_explicit_whole_result(ledger):
    put_receipt(ledger, "maker", task_link())
    put_receipt(ledger, "checker", task_link(stage="check", role="checker"))
    put_receipt(ledger, "repair", task_link(stage="repair", role="repair", workflow="replan"))
    close(ledger, "maker", "m", outcome="rejected")
    close(ledger, "checker", "c")
    assert project(ledger)["accepted_count"] == 0
    assert project(ledger)["tasks"]["t1"]["state"] == "unknown"
    final = close(ledger, "repair", "r", result=task_result(), outcome="accepted_after_repair", repairs=1)
    result = project(ledger)
    assert result["accepted_count"] == 1
    assert result["tasks"]["t1"]["terminal_judgment"] == "accepted_after_repair"
    assert result["tasks"]["t1"]["current_close_id"] == final["close_id"]
    assert result["total_cost"] is None
    assert result["tasks"]["t1"]["total_cost"] is None
    assert all("task_result" not in row for row in outcomes.read_outcomes(ledger[1])[:2])
    assert set(final["evidence"][0]) == set(ledger[2]) | {"verified_state", "verified_at", "verification_scope"}


def test_task_cross_same_decision_supersession_old_cli_invalidation_and_recovery(ledger):
    for decision in ("d1", "d2", "d3"):
        put_receipt(ledger, decision, task_link(stage=decision))
    close(ledger, "d1", "a", result=task_result())
    close(ledger, "d2", "b", result=task_result(supersedes="a"), outcome="rejected")
    assert project(ledger)["accepted_count"] == 0
    before = ledger[1].read_bytes()
    with pytest.raises(ValueError, match="decision already closed"):
        close(ledger, "d2", "missing-normal", result=task_result(supersedes="b"))
    assert ledger[1].read_bytes() == before
    close(ledger, "d2", "c", result=task_result(supersedes="b"), supersedes="b")
    close(ledger, "d2", "old", supersedes="c", outcome="rejected")
    unknown = project(ledger)["tasks"]["t1"]
    assert unknown["state"] == "unknown" and unknown["accepted_count"] == 0
    assert unknown["current_close_id"] == "old" and unknown["reason"] == "untagged_successor"
    close(ledger, "d3", "recovered", result=task_result(supersedes="old"))
    assert project(ledger)["accepted_count"] == 1
    rows = outcomes.read_outcomes(ledger[1])
    rows[-1]["recorded_at"] = "2000-01-01T00:00:00+00:00"
    rows[0]["recorded_at"] = "2099-01-01T00:00:00+00:00"
    assert outcomes.task_projection(receipts.read_receipts(ledger[0]), rows)["tasks"]["t1"]["current_close_id"] == "recovered"


def test_duplicate_cross_task_missing_and_fork_declarations_write_nothing(ledger):
    put_receipt(ledger, "d1", task_link())
    put_receipt(ledger, "d2", task_link())
    put_receipt(ledger, "other", task_link(task="t2"))
    close(ledger, "d1", "a", result=task_result())
    close(ledger, "d2", "b", result=task_result(supersedes="a"))
    for decision, result, supersedes in (
            ("d1", task_result(), "a"),
            ("d1", task_result(supersedes="a"), "a"),
            ("d1", task_result(supersedes="missing"), "a"),
            ("other", task_result(task="t2", supersedes="b"), None)):
        before = ledger[1].read_bytes()
        with pytest.raises(ValueError):
            close(ledger, decision, "bad", result=result, supersedes=supersedes)
        assert ledger[1].read_bytes() == before
    assert project(ledger)["accepted_count"] == 1


def test_task_close_replay_omitted_flag_exact_and_supplied_change_rejected(ledger):
    put_receipt(ledger, "d1", task_link())
    original = close(ledger, "d1", "a", result=task_result())
    before = ledger[1].read_bytes()
    assert close(ledger, "d1", "a") == original | {"replayed": True}
    assert close(ledger, "d1", "a", result=task_result()) == original | {"replayed": True}
    with pytest.raises(ValueError, match="changed inputs"):
        close(ledger, "d1", "a", result=task_result(supersedes="different"))
    assert ledger[1].read_bytes() == before
    put_receipt(ledger, "old", task_link())
    original = close(ledger, "old", "untagged")
    with pytest.raises(ValueError, match="changed inputs"):
        close(ledger, "old", "untagged", result=task_result())
    assert "task_result" not in original


@pytest.mark.parametrize("change", [
    {"v": 2}, {"consumer_task_id": "bad id"}, {"consumer_task_id": "other"},
    {"standard_evidence": []}, {"standard_evidence": [True]},
    {"standard_evidence": [1]}, {"standard_evidence": [0, 0]}, {"extra": True},
])
def test_invalid_task_result_has_no_outcome_write(ledger, change):
    put_receipt(ledger, "d", task_link())
    with pytest.raises(ValueError):
        close(ledger, "d", "bad", result=task_result() | change)
    assert not ledger[1].exists()


def test_task_result_requires_explicit_valid_receipt_link(ledger):
    put_receipt(ledger, "legacy")
    with pytest.raises(ValueError, match="task_link"):
        close(ledger, "legacy", "bad", result=task_result())
    assert not ledger[1].exists()
    malformed = {"schema_version": 1, "decision_id": "bad", "request": {}, "task_link": task_link() | {"role": "worker"}}
    before = ledger[0].read_bytes()
    with pytest.raises(ValueError):
        receipts.append_receipt(ledger[0], malformed)
    assert ledger[0].read_bytes() == before


def test_receipt_append_race_checks_only_supplied_extensions(ledger):
    parent = {"v": 1, "reason": {"code": "whole_result"}}
    original = {"schema_version": 1, "decision_id": "d", "request": {"same": True},
                "task_link": task_link(), "choice": {"v": 1, "caller": parent, "considered": []}}
    receipts.append_receipt(ledger[0], original)
    candidate = copy.deepcopy(original)
    candidate["choice"]["considered"] = [{"fresh_runtime": True}]
    assert receipts.append_receipt(ledger[0], candidate, {}) == original | {"replayed": True}
    assert receipts.append_receipt(ledger[0], candidate) == original | {"replayed": True}
    before = ledger[0].read_bytes()
    for key, changed in (("task_link", task_link(stage="changed")),
                         ("choice", {"v": 1, "caller": parent | {"reason": {"code": "changed"}}})):
        with pytest.raises(ValueError, match="changed or absent"):
            receipts.append_receipt(ledger[0], original | {key: changed})
    legacy = put_receipt(ledger, "old")
    with pytest.raises(ValueError, match="changed or absent"):
        receipts.append_receipt(ledger[0], legacy | {"task_link": task_link()})
    assert receipts.find_receipt(ledger[0], "d") == original
    assert before in ledger[0].read_bytes()


def resolve_cli(tmp_path, decision="d", *flags):
    return subprocess.run([str(TOOL), "resolve", "implement.standard", "--reason", "Synthetic fixture",
                           "--receipts", str(tmp_path / "receipts.jsonl"), "--record", "--decision-id", decision,
                           "--parent-model", "fixture", "--parent-provider-instance", "fixture", "--parent-thread", "fixture",
                           *flags], cwd=REPO, capture_output=True, text=True)


def test_cli_fresh_choice_and_early_replay_identity(tmp_path):
    tmp_path.chmod(0o700)
    parent = {"v": 1, "reason": {"code": "prefer_complete_workflow"},
              "schedule": {"state": "selected_waiting", "candidate": "dot", "reason": {"code": "busy"}}}
    first = resolve_cli(tmp_path, "d", "--task-link", json.dumps(task_link()), "--choice", json.dumps(parent))
    assert first.returncode == 0, first.stderr
    original = json.loads(first.stdout)
    assert original["schema_version"] == 1 and original["choice"]["v"] == 1
    assert original["choice"]["caller"] == parent
    assert "task_link" not in original["request"] and "choice" not in original["request"]
    before = (tmp_path / "receipts.jsonl").read_bytes()
    replay = resolve_cli(tmp_path)
    assert replay.returncode == 0, replay.stderr
    assert json.loads(replay.stdout) == original | {"replayed": True}
    same = resolve_cli(tmp_path, "d", "--task-link", json.dumps(task_link()), "--choice", json.dumps(parent))
    assert same.returncode == 0, same.stderr
    assert json.loads(same.stdout) == original | {"replayed": True}
    for flag, changed in (("--task-link", task_link(stage="other")),
                          ("--choice", parent | {"reason": {"code": "changed"}})):
        assert resolve_cli(tmp_path, "d", flag, json.dumps(changed)).returncode != 0
    assert (tmp_path / "receipts.jsonl").read_bytes() == before
    # Model a stored pre-upgrade receipt, without changing its legacy caller identity.
    legacy = original.copy()
    legacy["decision_id"] = "old"
    legacy.pop("choice")
    legacy.pop("task_link")
    receipts.append_receipt(tmp_path / "receipts.jsonl", legacy)
    old = resolve_cli(tmp_path, "old")
    assert old.returncode == 0, old.stderr
    assert json.loads(old.stdout) == legacy | {"replayed": True}
    assert resolve_cli(tmp_path, "old", "--task-link", json.dumps(task_link())).returncode != 0
    assert resolve_cli(tmp_path, "old", "--choice", json.dumps(parent)).returncode != 0


@pytest.mark.parametrize("flag,raw", [
    ("--task-link", json.dumps(task_link() | {"consumer_task_id": "bad id"})),
    ("--task-link", json.dumps(task_link() | {"v": True})),
    ("--task-link", '{"v":1,"v":1}'),
    ("--choice", '{"v":1,"reason":{"code":"x"},"extra":1}'),
    ("--choice", '{"v":1,"reason":{"code":"x"},"considered":[{"kind":"workflow","candidate":"x","disposition":"selected","reason":{"code":"x"},"predicted_total_cost_per_accepted_result":{"state":"known","value":1,"reason":"price"}}]}'),
])
def test_cli_invalid_extensions_before_ledger_or_quota_write(tmp_path, flag, raw):
    result = resolve_cli(tmp_path, "d", flag, raw)
    assert result.returncode != 0
    assert not list(tmp_path.iterdir())


def test_choice_trace_pointers_preserve_basis_dot_override_and_unknown_cost():
    arm = {"provider": "p", "model": "evaluated", "effort": "medium"}
    other = arm | {"model": "override"}
    value = {"routing": {
        "trace": [{"candidate": 0, "arm": arm, "instance": "i", "basis": "unvalidated", "removed_by": None, "rank_position": 0},
                  {"candidate": 1, "arm": other, "instance": None, "basis": "primary", "removed_by": "no_catalog_account"}],
        "chosen": {"candidate": 0, "arm": arm, "instance": "i"},
        "final": {"arm": other, "account": "j", "overridden": True, "target": {"model": "override"}},
        "destination": {"chosen": "model", "considered": [{"name": "dot", "state": "unavailable", "capacity": {"available": False, "reason": "busy"}}]}
    }}
    original = copy.deepcopy(value)
    parent = {"v": 1, "reason": {"code": "wait_for_whole_result"},
              "considered": [{"kind": "workflow", "candidate": "dot", "disposition": "selected_waiting", "reason": {"code": "busy"}}]}
    choice = receipts.capture_choice(value, receipts.validate_choice(parent))
    assert value == original
    assert len(choice["considered"]) == 5
    assert choice["considered"][0]["basis"] == "unvalidated"
    assert choice["considered"][0]["disposition"] == "overridden"
    assert choice["considered"][1]["reason"]["code"] == "no_catalog_account"
    assert choice["considered"][2]["disposition"] == "selected"
    assert choice["considered"][3]["candidate"] == "dot"
    assert choice["considered"][3]["reason"]["source"] == "#/routing/destination/considered/0"
    assert all(item[receipts.COST_KEY]["state"] == "unknown" and item[receipts.COST_KEY]["value"] is None for item in choice["considered"])
    assert "withhold dispatch" in choice["warning"]
    assert "unexamined" not in json.dumps(choice)
    value["routing"]["destination"]["chosen"] = "dot"
    assert receipts.capture_choice(value)["considered"][2]["disposition"] == "model_fallback"
    resolved = receipts.capture_choice({"selection": {"state": "catalog_present", "target": {"model": "one"}}, "alternatives": []})
    assert len(resolved["considered"]) == 1
    assert resolved["considered"][0]["reason"]["source"] == "#/selection"


def test_concurrent_duplicate_task_declarations_only_one_appends(ledger):
    for decision in ("a", "b"):
        put_receipt(ledger, decision, task_link(stage=decision))

    def submit(decision):
        try:
            close(ledger, decision, "close-" + decision, result=task_result())
            return "written"
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit, ("a", "b"))) == ["rejected", "written"]
    assert len(outcomes.read_outcomes(ledger[1])) == 1
    assert project(ledger)["accepted_count"] == 1


def test_legacy_top_level_compatibility_and_malformed_projection_unknown(ledger):
    put_receipt(ledger, "d", task_link())
    row = close(ledger, "d", "a", result=task_result())
    # Legacy validation deliberately ignores unknown top-level extensions.
    malformed = row | {"task_result": row["task_result"] | {"standard_evidence": [99]}, "future_extension": True}
    outcomes.check_row(malformed)
    result = outcomes.task_projection(receipts.read_receipts(ledger[0]), [malformed])
    assert result["tasks"]["t1"]["state"] == "unknown" and result["accepted_count"] == 0
    put_receipt(ledger, "other", task_link(stage="other"))
    duplicate = row | {"decision_id": "other", "close_id": "other-close"}
    result = outcomes.task_projection(receipts.read_receipts(ledger[0]), [row, duplicate])
    assert result["tasks"]["t1"]["state"] == "unknown" and result["accepted_count"] == 0


@pytest.mark.parametrize("command", ["route", "resolve"])
@pytest.mark.parametrize("claim", ["schedule", "considered"])
def test_compact_waiting_claim_prevents_launch_recipe(world, command, claim):
    import json

    caller = {"v": 1, "reason": {"code": "wait_for_whole_result"}}
    if claim == "schedule":
        caller["schedule"] = {"state": "selected_waiting", "candidate": "dot", "reason": {"code": "busy"}}
    else:
        caller["considered"] = [{"kind": "workflow", "candidate": "dot", "disposition": "selected_waiting", "reason": {"code": "busy"}}]
    extra = [] if command == "route" else ["--account-hint", "claude-work"]
    argv = [command, "implement.quota-tight", *world.args(), *record_flags("waiting-compact"), *extra,
            "--choice", json.dumps(caller)]
    full_run, full = world.cli(*argv)
    assert full_run.returncode == 0, full_run.stderr
    assert full["target"] is not None and full["launch_ready"] is True
    original = world.receipts.read_bytes()
    for replay_flags in (["--choice", json.dumps(caller)], []):
        replay_argv = argv[:-2] + replay_flags
        result, brief = world.cli(*replay_argv, "--brief")
        assert result.returncode == 0, result.stderr
        assert brief["target"] == full["target"]
        assert brief["dispatch"] is None
        assert brief["waiting"]["state"] == "selected_waiting"
        assert brief["waiting"]["assertion"] == "caller_claim"
        assert "withhold dispatch" in brief["waiting"]["warning"]
        assert brief["receipt"]["stored"] is True
        assert world.receipts.read_bytes() == original
    fresh = argv[:-2]
    fresh[fresh.index("waiting-compact")] = "waiting-unrecorded"
    fresh.remove("--record")
    result, brief = world.cli(*fresh, "--choice", json.dumps(caller), "--brief")
    assert result.returncode == 0, result.stderr
    assert brief["target"] == full["target"] and brief["dispatch"] is None
    assert brief["waiting"]["state"] == "selected_waiting"
    assert brief["receipt"]["stored"] is False
    assert world.receipts.read_bytes() == original
