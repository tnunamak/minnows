#!/usr/bin/env python3
"""Run a fixed local-eval / harness-smoke task via waspflow.

Results are harness_smoke by default (not quality evidence) unless
LOCAL_EVAL_QUALITY=1 is set *and* the task declares quality_eligible: true.

Usage:
  ./scripts/run_local_eval.py tasks/implement-standard-oracle-v1.json
  ./scripts/run_local_eval.py --all
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
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


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def hash_tree(root: Path, exclude: set[str] | None = None) -> dict[str, str]:
    exclude = exclude or set()
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(root))
        if any(rel == e or rel.startswith(e + "/") for e in exclude):
            continue
        if "__pycache__" in rel or rel.startswith(".pytest"):
            continue
        out[rel] = file_sha256(p)
    return out


def run_oracle(oracle: dict, cwd: Path, protected: dict[str, str] | None) -> tuple[bool, str]:
    # protected file integrity
    if protected:
        for rel, digest in protected.items():
            p = cwd / rel
            if not p.is_file():
                return False, f"protected file missing: {rel}"
            if file_sha256(p) != digest:
                return False, f"protected file modified: {rel}"
    t = oracle["type"]
    if t == "shell":
        timeout = int(oracle.get("timeout_sec", 120))
        try:
            r = subprocess.run(
                oracle["command"],
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return False, f"oracle timed out after {timeout}s"
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
    meta: dict = {"cmd": cmd, "started_at": datetime.now(timezone.utc).isoformat()}
    if dry_run:
        meta["dry_run"] = True
        meta["spawned_ok"] = True
        return meta
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    print("+", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, env=env, capture_output=True, text=True)
    meta["spawn_exit"] = r.returncode
    meta["spawn_stdout"] = (r.stdout or "")[-2000:]
    meta["spawn_stderr"] = (r.stderr or "")[-2000:]
    combined = (r.stdout or "") + (r.stderr or "")
    # Prefer explicit spawn success text; do NOT treat mere lane existence as ok
    # (lane_set runs before provider_spawn).
    spawned_ok = "spawned" in combined.lower() and "spawn aborted" not in combined.lower()
    if not spawned_ok and r.returncode == 0:
        spawned_ok = True
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
    st = subprocess.run(
        ["waspflow", "status", lane],
        env=env,
        capture_output=True,
        text=True,
    )
    try:
        meta["lane_status"] = json.loads(st.stdout)
    except json.JSONDecodeError:
        meta["lane_status"] = {"raw": (st.stdout or "")[-2000:]}
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
    run_id: str,
) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    exp = expansion["expands_to"]
    today = date.today().isoformat()
    quality = bool(task.get("quality_eligible")) and os.environ.get("LOCAL_EVAL_QUALITY") == "1"
    classification = "quality_eval" if quality else "harness_smoke"
    score = 1.0 if passed else 0.0
    metric_base = task["metric"]
    if not quality and not metric_base.startswith("smoke-"):
        metric_base = "smoke-" + metric_base.replace("local-", "")
    metric_id = metric_base.lower().replace(".", "-")
    row = {
        "model": exp.get("model"),
        "metric": metric_base,
        "score": score,
        "unit": "pass_rate" if quality else "other",
        "effort": exp.get("effort"),
        "mode": exp.get("mode", "standard"),
        "harness": "waspflow",
        "task_family": task.get("task_family"),
        "source_type": "local_eval",
        "evidence_grade": "A" if quality else "D",
        "observed_at": today,
        "metric_id": metric_id,
        "comparable": False,
        "comparability_group": f"{classification}::{metric_id}::{run_id}",
        "caveat": (
            f"{classification} task={task['id']} op={task['op']} "
            f"oracle={'pass' if passed else 'fail'} run_id={run_id}. {oracle_detail[:400]}"
        ),
        "confidence": {"type": "single_seed", "value": 0},
    }
    doc = {
        "id": f"local-{task['id']}-{run_id}"[:80],
        "schema_version": 1,
        "kind": "performance",
        "provider": "other",
        "retrieved_at": today,
        "source_urls": [
            "https://github.com/tnunamak/minnows/tree/main/data/local-evals"
        ],
        "source_ids": ["local-evals-waspflow-2026-07-09"],
        "notes": (
            f"{classification}. policy={expansion['policy_version']} "
            f"catalog_ref={expansion['catalog_ref']}. "
            + (
                "Quality-eligible run."
                if quality
                else "NOT model quality evidence (Sol+Fable P0.2)."
            )
        ),
        "scores": [row],
        "run": {
            "run_id": run_id,
            "classification": classification,
            "not_quality_evidence": not quality,
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
                    "spawned_ok",
                    "lane_status",
                    "dry_run",
                )
                if k in run_meta
            },
        },
    }
    # append-only filename with run_id
    out = RESULTS / f"{today}-{task['id']}-{exp.get('model')}-{exp.get('effort')}-{run_id[:8]}.json"
    out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return out


def run_one(task_path: Path, *, dry_run: bool) -> int:
    task = load_task(task_path)
    expansion = op_expansion(task["op"])
    fixture = LOCAL / task["fixture"]
    if not fixture.is_dir():
        print(f"missing fixture {fixture}", file=sys.stderr)
        return 2

    run_id = uuid.uuid4().hex
    work = Path(tempfile.mkdtemp(prefix=f"local-eval-{task['id']}-"))
    for item in fixture.iterdir():
        dest = work / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    # protected files: tests + optional list from task
    protect_globs = task.get("protected_files") or []
    if task["oracle"].get("type") == "shell" and not protect_globs:
        # default: protect test_* files
        protect_globs = [str(p.relative_to(work)) for p in work.rglob("test_*.py")]
    protected = {}
    for rel in protect_globs:
        p = work / rel
        if p.is_file():
            protected[rel] = file_sha256(p)

    report = None
    if task["oracle"].get("type") in ("file_contains", "file_equals"):
        report = task["oracle"]["path"]

    lane = f"leval-{task['id'][:20]}-{run_id[:8]}"
    lane = "".join(c if c.isalnum() or c in "._-" else "-" for c in lane)[:48]

    print(f"== {task['id']} op={task['op']} lane={lane} run_id={run_id}", flush=True)
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

    if not meta.get("spawned_ok"):
        print("spawn failed:", meta.get("spawn_stderr") or meta.get("spawn_stdout"), file=sys.stderr)
        passed, detail = False, f"spawn failed: {meta.get('spawn_stderr', '')[:500]}"
    else:
        passed, detail = run_oracle(task["oracle"], work, protected or None)

    out = emit_result(
        task=task,
        expansion=expansion,
        passed=passed,
        oracle_detail=detail,
        run_meta=meta,
        work_dir=work,
        run_id=run_id,
    )
    print(f"result: passed={passed} classification=harness_smoke -> {out}", flush=True)
    if passed:
        shutil.rmtree(work, ignore_errors=True)
    else:
        print(f"work dir retained: {work}", flush=True)
    return 0 if passed else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tasks", nargs="*", type=Path)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.all:
        paths = sorted((LOCAL / "tasks").glob("*.json"))
    else:
        paths = []
        for p in args.tasks:
            p = p if p.is_absolute() else REPO / p
            if not p.is_file():
                p = LOCAL / "tasks" / Path(p).name
            paths.append(p)
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
