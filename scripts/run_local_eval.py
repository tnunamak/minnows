#!/usr/bin/env python3
"""Run a fixed local-eval task via waspflow and emit performance-shaped results.

Usage:
  ./scripts/run_local_eval.py tasks/implement-standard-oracle-v1.json
  ./scripts/run_local_eval.py --all
  ./scripts/run_local_eval.py --dry-run tasks/...

Never invents scores: oracle is measured on disk after the agent finishes.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOCAL = REPO / "data" / "local-evals"
RESULTS = LOCAL / "results"
POLICY = REPO / "data" / "model-choice-policy" / "operating-points.json"


def load_task(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def op_expansion(op_id: str) -> dict:
    pol = json.loads(POLICY.read_text(encoding="utf-8"))
    for row in pol["operating_points"]:
        if row["id"] == op_id:
            return {
                "expands_to": row["expands_to"],
                "policy_version": pol["policy_version"],
                "catalog_ref": pol["catalog_ref"],
            }
    raise SystemExit(f"unknown op {op_id}")


def run_oracle(oracle: dict, cwd: Path) -> tuple[bool, str]:
    t = oracle["type"]
    if t == "shell":
        r = subprocess.run(
            oracle["command"],
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        ok = r.returncode == int(oracle.get("expect_exit", 0))
        detail = (r.stdout + r.stderr)[-2000:]
        return ok, f"exit={r.returncode}\n{detail}"
    if t == "file_contains":
        p = cwd / oracle["path"]
        if not p.is_file():
            return False, f"missing {oracle['path']}"
        text = p.read_text(encoding="utf-8", errors="replace")
        missing = [s for s in oracle["must_contain"] if s not in text]
        if missing:
            return False, f"missing tokens: {missing}\n---\n{text[:1500]}"
        return True, f"found all tokens in {oracle['path']}"
    if t == "file_equals":
        p = cwd / oracle["path"]
        if not p.is_file():
            return False, f"missing {oracle['path']}"
        got = p.read_text(encoding="utf-8", errors="replace").strip()
        exp = str(oracle["equals"]).strip()
        # allow trailing comments / whitespace-only extras on first line
        first = got.splitlines()[0].strip() if got else ""
        ok = got == exp or first == exp
        return ok, f"got={got!r} expected={exp!r}"
    return False, f"unknown oracle type {t}"


def waspflow_spawn_wait(
    *,
    op: str,
    lane: str,
    cwd: Path,
    prompt: str,
    report: str | None,
    timeout: int,
    dry_run: bool,
) -> dict:
    cmd = [
        "waspflow",
        "spawn",
        "--op",
        op,
        "--lane",
        lane,
        "--cwd",
        str(cwd),
    ]
    if report:
        cmd += ["--report", report]
    cmd += ["--", prompt]
    meta = {"cmd": cmd, "started_at": datetime.now(timezone.utc).isoformat()}
    if dry_run:
        meta["dry_run"] = True
        return meta
    # Unset ANTHROPIC_API_KEY so Claude uses subscription if available
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    print("+", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, env=env, capture_output=True, text=True)
    meta["spawn_exit"] = r.returncode
    meta["spawn_stdout"] = (r.stdout or "")[-2000:]
    meta["spawn_stderr"] = (r.stderr or "")[-2000:]
    # waspflow currently exits non-zero after a successful spawn in some builds;
    # treat presence of a live/known lane (or "spawned" log) as success.
    combined = (r.stdout or "") + (r.stderr or "")
    st_probe = subprocess.run(
        ["waspflow", "status", lane], env=env, capture_output=True, text=True
    )
    lane_live = False
    try:
        stj = json.loads(st_probe.stdout or "{}")
        lane_live = stj.get("status") in ("live", "idle", "exited", "reaped") or bool(
            stj.get("session_id") or stj.get("provider")
        )
    except json.JSONDecodeError:
        pass
    spawned_ok = (
        r.returncode == 0
        or "spawned" in combined.lower()
        or lane_live
    )
    meta["spawned_ok"] = spawned_ok
    if not spawned_ok:
        return meta
    wait = subprocess.run(
        ["waspflow", "wait", lane, "--timeout", str(timeout)],
        env=env,
        capture_output=True,
        text=True,
    )
    meta["wait_exit"] = wait.returncode
    meta["wait_stdout"] = (wait.stdout or "")[-1000:]
    # lane status for provenance
    st = subprocess.run(
        ["waspflow", "status", lane],
        env=env,
        capture_output=True,
        text=True,
    )
    try:
        meta["lane_status"] = json.loads(st.stdout)
    except json.JSONDecodeError:
        meta["lane_status"] = {"raw": st.stdout[-2000:]}
    # reap
    subprocess.run(["waspflow", "reap", lane], env=env, capture_output=True, text=True)
    meta["finished_at"] = datetime.now(timezone.utc).isoformat()
    return meta


def emit_result(
    *,
    task: dict,
    expansion: dict,
    passed: bool,
    oracle_detail: str,
    run_meta: dict,
    work_dir: Path,
) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    exp = expansion["expands_to"]
    today = date.today().isoformat()
    score = 1.0 if passed else 0.0
    row = {
        "model": exp.get("model"),
        "metric": task["metric"],
        "score": score,
        "unit": "pass_rate",
        "effort": exp.get("effort"),
        "mode": exp.get("mode", "standard"),
        "harness": "waspflow",
        "task_family": task.get("task_family"),
        "source_type": "local_eval",
        "evidence_grade": "A",
        "observed_at": today,
        "metric_id": task["metric"].lower().replace(".", "-"),
        "caveat": (
            f"local-eval task={task['id']} op={task['op']} "
            f"oracle={'pass' if passed else 'fail'}. {oracle_detail[:400]}"
        ),
    }
    doc = {
        "id": f"local-{task['id']}-{today}".replace(".", "-"),
        "schema_version": 1,
        "kind": "performance",
        "provider": "other",
        "retrieved_at": today,
        "source_urls": [
            "https://github.com/tnunamak/minnows/tree/main/data/local-evals"
        ],
        "source_ids": [],
        "notes": (
            f"Fixed-harness local eval. policy={expansion['policy_version']} "
            f"catalog_ref={expansion['catalog_ref']}. "
            "Not a vendor board — measured on Tim's waspflow harness."
        ),
        "scores": [row],
        "run": {
            "op": task["op"],
            "task_id": task["id"],
            "policy_version": expansion["policy_version"],
            "catalog_ref": expansion["catalog_ref"],
            "expands_to": exp,
            "work_dir": str(work_dir),
            "passed": passed,
            "oracle_detail": oracle_detail[:2000],
            "meta": {
                k: run_meta[k]
                for k in (
                    "started_at",
                    "finished_at",
                    "spawn_exit",
                    "wait_exit",
                    "lane_status",
                    "dry_run",
                )
                if k in run_meta
            },
        },
    }
    out = RESULTS / f"{today}-{task['id']}-{exp.get('model')}-{exp.get('effort')}.json"
    out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return out


def run_one(task_path: Path, *, dry_run: bool) -> int:
    task = load_task(task_path)
    expansion = op_expansion(task["op"])
    fixture = LOCAL / task["fixture"]
    if not fixture.is_dir():
        print(f"missing fixture {fixture}", file=sys.stderr)
        return 2

    work = Path(tempfile.mkdtemp(prefix=f"local-eval-{task['id']}-"))
    # copy fixture contents into work
    for item in fixture.iterdir():
        dest = work / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    report = None
    if task["oracle"].get("type") in ("file_contains", "file_equals"):
        report = task["oracle"]["path"]

    lane = f"leval-{task['id'][:24]}-{int(time.time()) % 100000}"
    # sanitize lane name
    lane = "".join(c if c.isalnum() or c in "._-" else "-" for c in lane)[:48]

    print(f"== {task['id']} op={task['op']} lane={lane} cwd={work}", flush=True)
    meta = waspflow_spawn_wait(
        op=task["op"],
        lane=lane,
        cwd=work,
        prompt=task["prompt"],
        report=report,
        timeout=int(task.get("timeout_sec", 600)),
        dry_run=dry_run,
    )
    if dry_run:
        print(json.dumps({"dry_run": True, "task": task["id"], "expansion": expansion}, indent=2))
        shutil.rmtree(work, ignore_errors=True)
        return 0

    if not meta.get("spawned_ok", meta.get("spawn_exit", 1) == 0):
        print("spawn failed:", meta.get("spawn_stderr") or meta.get("spawn_stdout"), file=sys.stderr)
        passed, detail = False, f"spawn failed: {meta.get('spawn_stderr', '')[:500]}"
    else:
        passed, detail = run_oracle(task["oracle"], work)

    out = emit_result(
        task=task,
        expansion=expansion,
        passed=passed,
        oracle_detail=detail,
        run_meta=meta,
        work_dir=work,
    )
    print(f"result: passed={passed} -> {out}", flush=True)
    print(detail[:500], flush=True)
    # keep work dir for forensics on fail
    if passed:
        shutil.rmtree(work, ignore_errors=True)
    else:
        print(f"work dir retained: {work}", flush=True)
    return 0 if passed else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tasks", nargs="*", type=Path, help="task JSON paths")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    paths: list[Path] = []
    if args.all:
        paths = sorted((LOCAL / "tasks").glob("*.json"))
    else:
        paths = [p if p.is_absolute() else REPO / p for p in args.tasks]
        # also allow bare names under tasks/
        paths = [
            p if p.is_file() else LOCAL / "tasks" / p.name
            for p in paths
        ]
    if not paths:
        ap.error("need task paths or --all")
    rc = 0
    for p in paths:
        if not p.is_file():
            print(f"missing {p}", file=sys.stderr)
            rc = 2
            continue
        r = run_one(p, dry_run=args.dry_run)
        if r != 0:
            rc = r
    return rc


if __name__ == "__main__":
    sys.exit(main())
