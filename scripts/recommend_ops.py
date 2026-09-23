#!/usr/bin/env python3
"""Deterministic operating-point recommender for the model-choice-policy pack.

Reads the model-catalog pack and `op-requirements.json`, and for each op
derives a recommended (provider, model, effort) arm, or reports
INSUFFICIENT_EVIDENCE with the exact (model, metric) coverage that would
decide it. It never edits operating-points.json.

Stdlib only.

Usage:
  ./scripts/recommend_ops.py                 # markdown table + per-op detail
  ./scripts/recommend_ops.py --json          # machine-readable result
  ./scripts/recommend_ops.py --op review.audit
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = REPO / "data" / "model-catalog"
DEFAULT_POLICY = REPO / "data" / "model-choice-policy"

# Lane CLI -> (models.json provider, capabilities surface of that CLI).
LANES = {
    "claude": ("anthropic", "claude_code"),
    "codex": ("openai", "codex_cli"),
    "grok": ("xai", "grok_cli"),
    "antigravity": (None, "antigravity_cli"),
    "qwen": ("alibaba", "qwen_code"),
    "deepseek": ("deepseek", "deepseek_cli"),
}
# agy accepts these dispatch ids. Suffixes on Gemini ids encode effort.
ANTIGRAVITY_MODELS = {
    **{f"gemini-{v}-flash-{eff}": (f"gemini-{v}-flash", eff)
       for v in ("3.6", "3.7", "3.8") for eff in ("low", "medium", "high")},
    **{f"gemini-3.1-pro-{eff}": ("gemini-3.1-pro", eff) for eff in ("low", "high")},
    "claude-sonnet-4-6": ("claude-sonnet-4-6", None),
    "claude-opus-4-6-thinking": ("claude-opus-4-6", None),
    "gpt-oss-120b-medium": ("gpt-oss-120b", "medium"),
}
# "default" = the model takes no effort parameter (e.g. claude-haiku-4-5); its rows carry effort null.
EFFORTS = ("default", "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
ABOVE_DEFAULT_EFFORTS = frozenset({"xhigh", "max", "ultra"})
GRADE_RANK = {"A": 3, "B": 2, "C": 1, "D": 0}
INDEPENDENT_SOURCE_TYPES = frozenset({"third_party_board", "third_party_eval", "local_eval"})
VENDOR_SOURCE_TYPES = frozenset({"vendor_table", "digitized_chart"})
SUCCESS_UNITS = frozenset({"accuracy", "pass_rate", "error_rate"})
CONSTRAINT_RE = re.compile(r"^(different_model_family_from|different_vendor_from):([a-z0-9.-]+)$")
EPS = 1e-9


class RequirementsError(ValueError):
    """op-requirements.json is inconsistent with the catalog or policy."""


def parse_constraint(c: str) -> tuple[str, str]:
    """'different_vendor_from:implement.standard' -> ('different_vendor_from', 'implement.standard')."""
    m = CONSTRAINT_RE.match(c)
    if not m:
        raise RequirementsError(f"bad constraint {c!r}")
    return m.group(1), m.group(2)


# ---------------------------------------------------------------- catalog


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def version_key(model_id: str) -> tuple[float, ...]:
    """Numeric generation key parsed from a canonical id (claude-opus-5-5 -> (5, 5)).

    The catalog has no release-date field; this is used only to pick the
    newest reachable GA model within one (lane, provider, tier).
    """
    # Dotted versions are decimals as vendors use them ("grok-4.20" is 4.2, older than
    # grok-4.7); dash chains stay integer parts (claude-opus-5-5 -> (5, 5)); date-like
    # suffixes (0309, 2026, 20251001) are snapshot stamps, not generations.
    parts: list[float] = []
    for tok in re.findall(r"\d+(?:\.\d+)?", model_id):
        if "." in tok:
            parts.append(float(tok))
        elif len(tok) < 4:
            parts.append(float(int(tok)))
    return tuple(parts)


@dataclass
class Catalog:
    models: dict[str, dict]
    aliases: dict[str, str]
    metrics: dict[str, dict]
    surfaces: dict[tuple[str, str], list[str]]
    rows: list[dict]
    list_price: dict[str, float]
    publishers: dict[str, str]

    @property
    def effortless(self) -> set[str]:
        """Models the catalog marks as taking no effort parameter (effort_parameter: false)."""
        return {mid for mid, m in self.models.items() if m.get("effort_parameter") is False}

    @classmethod
    def from_dir(cls, root: Path) -> "Catalog":
        models_doc = _load(root / "models.json")
        models = {m["id"]: m for m in models_doc["models"]}
        aliases: dict[str, str] = {}
        for m in models_doc["models"]:
            for a in m.get("aliases") or []:
                aliases[a] = m["id"]
        metrics = {m["id"]: m for m in _load(root / "metrics.json")["metrics"]}
        surfaces: dict[tuple[str, str], list[str]] = {}
        for path in sorted((root / "capabilities").glob("*.json")):
            for s in _load(path).get("surfaces", []):
                surfaces[(s["model"], s["surface"])] = list(s.get("valid_efforts") or [])
        rows: list[dict] = []
        for path in sorted((root / "performance").glob("*.json")):
            for s in _load(path).get("scores", []):
                rows.append({**s, "_file": path.name})
        list_price: dict[str, float] = {}
        for path in sorted((root / "pricing").glob("*.json")):
            doc = _load(path)
            if doc.get("kind") != "api_usd":
                continue
            for mid, rate in doc.get("models", {}).items():
                if isinstance(rate.get("output_per_m"), (int, float)):
                    list_price.setdefault(mid, float(rate["output_per_m"]))
        publishers = {}
        src_path = root / "SOURCES.json"
        if src_path.is_file():
            publishers = {s["id"]: s.get("publisher", "") for s in _load(src_path)["sources"]}
        return cls(models, aliases, metrics, surfaces, rows, list_price, publishers)

    def resolve(self, model: str) -> str | None:
        if model in self.models:
            return model
        if model in self.aliases:
            return self.aliases[model]
        if "@" in model:
            return self.resolve(model.split("@", 1)[0])
        return None

    def publisher_vendor(self, source_id: str | None) -> str | None:
        pub = self.publishers.get(source_id or "", "").lower()
        for needle, vendor in (("openai", "openai"), ("anthropic", "anthropic"),
                               ("xai", "xai"), ("google", "google")):
            if needle in pub:
                return vendor
        return None


# ------------------------------------------------------------ requirements


def load_requirements(path: Path, catalog: Catalog, op_ids: set[str]) -> dict:
    """Load op-requirements.json, merge defaults, and fail loudly on any bad slot."""
    doc = _load(path)
    defaults = doc.get("defaults", {})
    restrictions = defaults.get("provider_restrictions", {})
    family_map = doc.get("task_families", {})
    errs: list[str] = []
    ops: dict[str, dict] = {}
    if "bar" in defaults or "evidence_metrics" in defaults:
        errs.append("defaults: bar and evidence_metrics are derived, not owner inputs")
    for family_op in family_map:
        if family_op not in op_ids:
            errs.append(f"task_families: unknown op {family_op!r}")
    for raw in doc.get("ops", []):
        op = raw.get("op")
        req = {k: v for k, v in defaults.items() if k != "provider_restrictions"}
        req.update(raw)
        if "allowed_providers" not in raw:
            req["allowed_providers"] = [p for p in defaults.get("allowed_providers", [])
                                        if p not in restrictions or op in restrictions[p]]
        where = f"op {op!r}"
        if op not in op_ids:
            errs.append(f"{where}: not an operating-points.json id")
        if op in ops:
            errs.append(f"{where}: duplicate entry")
        families = family_map.get(op, [])
        if not families:
            errs.append(f"{where}: task_families must be non-empty")
        req["task_families"] = families
        req["evidence_metrics"] = sorted(
            mid for mid, metric in catalog.metrics.items()
            if metric.get("task_family") in families
        )
        if not req["evidence_metrics"]:
            errs.append(f"{where}: no metrics tagged for task families {families}")
        for p in req.get("allowed_providers", []):
            if p not in LANES:
                errs.append(f"{where}: unknown provider {p!r}")
            elif p in restrictions and op not in restrictions[p]:
                errs.append(f"{where}: provider {p!r} is restricted to {restrictions[p]}")
        for e in req.get("allowed_efforts", []):
            if e not in EFFORTS:
                errs.append(f"{where}: unknown effort {e!r}")
            elif e in ABOVE_DEFAULT_EFFORTS and not req.get("effort_override_reason"):
                errs.append(f"{where}: effort {e!r} needs an owner effort_override_reason (rule 1: never xhigh/max by default)")
        if "bar" in raw or "evidence_metrics" in raw:
            errs.append(f"{where}: bar and evidence_metrics are derived, not owner inputs")
        for key in ("attempt_overhead_usd", "silent_failure_cost_usd", "failure_detection_probability"):
            value = req.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
                errs.append(f"{where}: {key} must be a finite nonnegative number")
        d = req.get("failure_detection_probability")
        if isinstance(d, (int, float)) and d > 1:
            errs.append(f"{where}: failure_detection_probability must be in 0..1")
        if op in {"review.audit", "advisor.deep"} and d != 0:
            errs.append(f"{where}: judged ops require failure_detection_probability = 0")
        for c in req.get("constraints", []):
            m = CONSTRAINT_RE.match(c)
            if not m:
                errs.append(f"{where}: bad constraint {c!r}")
            elif m.group(2) not in op_ids:
                errs.append(f"{where}: constraint references unknown op {m.group(2)!r}")
        if req.get("cost_basis") != "usd_per_task":
            errs.append(f"{where}: only cost_basis 'usd_per_task' is implemented")
        if req.get("min_evidence_grade") not in GRADE_RANK:
            errs.append(f"{where}: min_evidence_grade must be one of {sorted(GRADE_RANK)}")
        ops[op] = req
    if errs:
        raise RequirementsError("\n".join(errs))
    order = dependency_order(ops)
    return {"doc": doc, "defaults": defaults, "ops": ops, "order": order}


def op_dependencies(req: dict) -> list[str]:
    deps = []
    for c in req.get("constraints", []):
        deps.append(parse_constraint(c)[1])
    return deps


def dependency_order(ops: dict[str, dict]) -> list[str]:
    """Topological order so makers resolve before their checkers."""
    order: list[str] = []
    state: dict[str, int] = {}

    def visit(op: str, path: tuple[str, ...]) -> None:
        if state.get(op) == 2:
            return
        if state.get(op) == 1:
            raise RequirementsError(f"constraint cycle: {' -> '.join(path + (op,))}")
        state[op] = 1
        for dep in op_dependencies(ops[op]) if op in ops else []:
            visit(dep, path + (op,))
        state[op] = 2
        order.append(op)

    for op in ops:
        visit(op, ())
    return order


# --------------------------------------------------------------- arms


@dataclass(frozen=True, order=True)
class Arm:
    model: str
    effort: str
    lane: str
    dispatch_model: str | None = None

    def label(self) -> str:
        return f"{self.lane}/{self.dispatch_model or self.model}/{self.effort}"


def arm_dict(arm: Arm | None) -> dict | None:
    if arm is None:
        return None
    out = {"provider": arm.lane, "model": arm.dispatch_model or arm.model, "effort": arm.effort}
    if arm.dispatch_model:
        out["catalog_model"] = arm.model
    return out


def dispatches_for_lane(lane: str, model: dict) -> list[tuple[str, str | None]]:
    """Return dispatch ids and fixed efforts for a catalog model on one lane."""
    if LANES[lane][0] == model["provider"]:
        return [(model["id"], None)]
    if lane == "antigravity":
        return [(slug, effort) for slug, (mid, effort) in ANTIGRAVITY_MODELS.items()
                if mid == model["id"]]
    return []


def candidate_arms(req: dict, catalog: Catalog, makers: dict[str, Arm]) -> tuple[list[Arm], list[dict], list[str]]:
    """Step 1: GA + tier + reachable lane + newest on that lane + valid CLI effort."""
    arms: list[Arm] = []
    excluded: list[dict] = []
    notes: list[str] = []
    allowed_lanes = req["allowed_providers"]

    newest: dict[tuple[str, str, str], str] = {}
    for m in catalog.models.values():
        if m.get("status") == "ga" and m.get("tier"):
            for lane in allowed_lanes:
                if dispatches_for_lane(lane, m):
                    key = (lane, m["provider"], m["tier"])
                    if key not in newest or version_key(m["id"]) > version_key(newest[key]):
                        newest[key] = m["id"]

    forbidden: list[tuple[str, str, str]] = []
    for c in req.get("constraints", []):
        kind, maker_op = parse_constraint(c)
        maker = makers.get(maker_op)
        if maker is None:
            continue
        mm = catalog.models[maker.model]
        if kind == "different_vendor_from":
            forbidden.append(("provider", mm["provider"], c))
        else:
            forbidden.append(("family", mm["family"], c))

    for m in sorted(catalog.models.values(), key=lambda x: x["id"]):
        reachable = [(lane, dispatches_for_lane(lane, m)) for lane in allowed_lanes]
        reachable = [(lane, dispatches) for lane, dispatches in reachable if dispatches]
        if not reachable:
            continue
        mid = m["id"]
        if m.get("status") != "ga":
            continue
        if m.get("access") == "restricted":
            excluded.append({"model": mid, "reason": "access restricted (not generally dispatchable)"})
            continue
        if not m.get("tier"):
            excluded.append({"model": mid, "reason": "no tier in models.json"})
            continue
        hit = next((c for key, value, c in forbidden if m.get(key) == value), None)
        if hit:
            excluded.append({"model": mid, "reason": f"constraint {hit}"})
            continue
        for lane, dispatches in reachable:
            if newest[(lane, m["provider"], m["tier"])] != mid:
                excluded.append({"model": mid, "lane": lane, "reason":
                                 f"superseded in tier {m['tier']!r} by {newest[(lane, m['provider'], m['tier'])]}"})
                continue
            surface = LANES[lane][1]
            for dispatch, fixed_effort in dispatches:
                efforts = catalog.surfaces.get((mid, surface))
                if efforts is None:
                    efforts = catalog.surfaces.get((mid, "api"))
                    if efforts is None:
                        if mid in catalog.effortless:
                            notes.append(f"{lane}/{mid}: takes no effort parameter; one arm at effort 'default'")
                            arms.append(Arm(mid, "default", lane, dispatch if dispatch != mid else None))
                            continue
                        excluded.append({"model": mid, "lane": lane, "reason": f"no {surface} or api effort surface"})
                        continue
                    notes.append(f"{lane}/{mid}: no {surface} surface; efforts taken from the api surface")
                usable = [e for e in req["allowed_efforts"] if e in efforts and (fixed_effort is None or e == fixed_effort)]
                if not usable:
                    excluded.append({"model": mid, "lane": lane, "reason": f"no allowed effort valid on its surface ({efforts})"})
                    continue
                arms.extend(Arm(mid, e, lane, dispatch if dispatch != mid else None) for e in usable)
    return arms, excluded, notes


# ------------------------------------------------------------- groups


@dataclass
class Cell:
    score: float  # normalized: higher is better
    raw: float
    cost: float | None
    row: dict


@dataclass
class Group:
    gid: str
    metric_id: str
    direction: str
    unit: str
    source_types: set[str]
    publisher_vendor: str | None
    vendors: set[str]
    cells: dict[tuple[str, str], Cell]
    conflicts: list[str] = field(default_factory=list)

    def models_present(self, arms: list["Arm"]) -> int:
        return len({a.model for a in arms})

    @property
    def cross_vendor_vendor_chart(self) -> bool:
        return bool(self.source_types & VENDOR_SOURCE_TYPES) and len(self.vendors) > 1

    def weight(self, req: dict) -> float:
        weights = req["source_weights"]
        w = min(weights.get(st, 1) for st in self.source_types)
        if self.cross_vendor_vendor_chart:
            w *= req["vendor_cross_vendor_factor"]
        return w

    def kind(self) -> str:
        if self.source_types & INDEPENDENT_SOURCE_TYPES and not self.source_types & VENDOR_SOURCE_TYPES:
            return "independent"
        return "vendor" if self.source_types & VENDOR_SOURCE_TYPES else "other"


def build_groups(req: dict, catalog: Catalog) -> tuple[list[Group], list[str]]:
    """Collect comparability groups for the op's evidence metrics (step 2 input)."""
    skipped: list[str] = []
    min_grade = GRADE_RANK[req["min_evidence_grade"]]
    metrics = set(req["evidence_metrics"])
    raw: dict[str, list[dict]] = {}
    for r in catalog.rows:
        if r.get("metric_id") in metrics and r.get("comparability_group"):
            raw.setdefault(r["comparability_group"], []).append(r)
    groups = []
    for gid, rows in sorted(raw.items()):
        mids = {r["metric_id"] for r in rows}
        units = {r["unit"] for r in rows}
        if len(mids) > 1 or len(units) > 1:
            skipped.append(f"{gid}: mixed metric_id/unit {sorted(mids)} {sorted(units)}")
            continue
        metric_id = mids.pop()
        direction = catalog.metrics[metric_id].get("direction")
        if direction not in ("higher_better", "lower_better"):
            skipped.append(f"{gid}: direction {direction!r} is not rankable")
            continue
        kept = [r for r in rows if GRADE_RANK.get(str(r.get("evidence_grade")), -1) >= min_grade]
        if not kept:
            skipped.append(f"{gid}: every row below evidence grade {req['min_evidence_grade']}")
            continue
        g = Group(
            gid=gid, metric_id=metric_id, direction=direction, unit=units.pop(),
            source_types={r["source_type"] for r in kept},
            publisher_vendor=next((v for v in (catalog.publisher_vendor(r.get("source_id")) for r in kept) if v), None),
            vendors=set(), cells={},
        )
        for r in kept:
            mid = catalog.resolve(r["model"])
            if mid is None:
                continue
            g.vendors.add(catalog.models[mid]["provider"])
            effort = r.get("effort")
            if effort is None and mid in catalog.effortless:
                effort = "default"
            if effort not in EFFORTS:
                continue  # effort unattributed (None/unknown/adaptive): cannot map to an arm
            cost = r.get("cost") if isinstance(r.get("cost"), dict) else None
            cost_v = cost["value"] if cost and cost.get("unit") == req["cost_basis"] else None
            raw_score = float(r["score"])
            cell = Cell(raw_score if direction == "higher_better" else -raw_score, raw_score, cost_v, r)
            key = (mid, effort)
            prev = g.cells.get(key)
            same_day_richer = (prev is not None and r.get("observed_at", "") == prev.row.get("observed_at", "")
                               and cost_v is not None and prev.cost is None)
            if prev is None or same_day_richer or r.get("observed_at", "") > prev.row.get("observed_at", ""):
                if prev is not None and prev.raw != raw_score:
                    g.conflicts.append(f"{mid}@{effort}: {prev.raw} ({prev.row['observed_at']}) superseded by {raw_score} ({r.get('observed_at')})")
                g.cells[key] = cell
            elif prev.raw != raw_score:
                g.conflicts.append(f"{mid}@{effort}: kept {prev.raw} ({prev.row['observed_at']}), ignored {raw_score} ({r.get('observed_at')})")
        groups.append(g)
    return groups, skipped


# ----------------------------------------------------------- evaluation


@dataclass
class GroupEval:
    group: Group
    weight: float
    present: list[Arm]
    ceiling_value: float | None  # best allowed-effort score; only screens missing efforts
    dominated: list[Arm]
    choice: Arm | None
    unpriced: list[Arm]
    unusable: str | None = None


def success_probability(g: Group, cell: Cell) -> float | None:
    """Convert fractional success and error rates to a success probability."""
    if g.unit not in SUCCESS_UNITS or not 0 <= cell.raw <= 1:
        return None
    return cell.raw if g.direction == "higher_better" else 1 - cell.raw


def expected_cost(g: Group, cell: Cell, req: dict) -> float | None:
    p = success_probability(g, cell)
    if p is None or cell.cost is None:
        return None
    d = req["failure_detection_probability"]
    terminal = 1 - (1 - p) * d
    if terminal == 0:
        return float("inf")
    attempts = (cell.cost + req["attempt_overhead_usd"]) / terminal
    silent_failure = (1 - p) * (1 - d) / terminal
    return attempts + silent_failure * req["silent_failure_cost_usd"]


def report_cost(value: float | None) -> float | str | None:
    return "Infinity" if value == float("inf") else value


def arm_sort_key(arm: Arm, cost: float, catalog: Catalog) -> tuple:
    """Rank by E, then list price, model generation, and effort."""
    price = catalog.list_price.get(arm.model, float("inf"))
    return (cost, price, tuple(-x for x in version_key(arm.model)), EFFORTS.index(arm.effort))


def evaluate_group(g: Group, arms: list[Arm], req: dict, catalog: Catalog) -> GroupEval:
    """Compare expected costs inside one group; retain a score ceiling for unmeasured efforts."""
    present = [a for a in arms if (a.model, a.effort) in g.cells]
    ev = GroupEval(g, g.weight(req), present, None, [], None, [])
    if not present:
        return ev
    ev.ceiling_value = max(g.cells[(a.model, a.effort)].score for a in present)
    if len({a.model for a in present}) < 2 and len({(a.model, a.effort) for a in present}) < 2:
        ev.unusable = "one candidate arm only; nothing to compare"
        return ev
    cell_of = {a: g.cells[(a.model, a.effort)] for a in present}
    if g.unit not in SUCCESS_UNITS or any(success_probability(g, c) is None for c in cell_of.values()):
        ev.unusable = "non-success-rate metric; ceiling screen only"
        return ev
    costs = {a: expected_cost(g, c, req) for a, c in cell_of.items()}
    ev.unpriced = [a for a in present if costs[a] is None]
    pool = [a for a in present if costs[a] is not None and costs[a] != float("inf")]
    if pool:
        ev.choice = min(pool, key=lambda a: arm_sort_key(a, costs[a], catalog))
    ev.dominated = [a for a in pool if any(
        cell_of[b].score >= cell_of[a].score and cell_of[b].cost <= cell_of[a].cost
        and (cell_of[b].score > cell_of[a].score or cell_of[b].cost < cell_of[a].cost)
        for b in pool if b != a)]
    return ev


def wins(arm: Arm, ev: GroupEval) -> bool:
    """The arm is the group's deterministic lowest-E choice."""
    return arm == ev.choice


def group_ref(ev: GroupEval) -> str:
    flag = "; vendor chart ranks rival vendor" if ev.group.cross_vendor_vendor_chart else ""
    return f"{ev.group.gid} [{'/'.join(sorted(ev.group.source_types))}, w={ev.weight:g}{flag}]"


def decide(op: str, req: dict, catalog: Catalog, makers: dict[str, Arm], current: dict) -> dict:
    """Steps 1-5 for one op."""
    arms, excluded, notes = candidate_arms(req, catalog, makers)
    result: dict[str, Any] = {
        "op": op,
        "current": current,
        "status": None,
        "recommended": None,
        "confidence": None,
        "deciding_groups": [],
        "disagreements": [],
        "missing": [],
        "flags": list(notes),
        "candidates": [a.label() for a in arms],
        "excluded_models": excluded,
        "groups": [],
    }
    for c in req.get("constraints", []):
        maker_op = parse_constraint(c)[1]
        if maker_op in makers:
            result["flags"].append(f"{c}: maker arm {makers[maker_op].label()}")
    maker = makers.get("implement.standard") if op == "review.audit" else None
    if not arms:
        result["status"] = "NO_CANDIDATES"
        return result

    groups, skipped = build_groups(req, catalog)
    result["skipped_groups"] = skipped
    evals = [evaluate_group(g, arms, req, catalog) for g in groups]
    evals = [ev for ev in evals if ev.present]

    # Ceiling rule: a candidate model with no allowed-effort score in a usable group, whose
    # best score there at ANY effort (incl. efforts the op disallows, e.g. max) is below the
    # group's best allowed-effort score, cannot reach that score at a lower effort.
    # This assumes quality does not rise as effort falls. An independent group
    # excludes the model; a vendor group only flags it.
    candidate_models = {a.model for a in arms}
    ceiling_excluded: dict[str, list[str]] = {}
    for ev in evals:
        if ev.ceiling_value is None:
            continue
        present_models = {a.model for a in ev.present}
        best: dict[str, tuple[float, str]] = {}
        for (mid, eff), cell in ev.group.cells.items():
            if mid in candidate_models and mid not in present_models:
                if mid not in best or cell.score > best[mid][0]:
                    best[mid] = (cell.score, eff)
        for mid, (score, eff) in sorted(best.items()):
            if score >= ev.ceiling_value - EPS:
                continue
            raw = score if ev.group.direction == "higher_better" else -score
            ceiling_raw = ev.ceiling_value if ev.group.direction == "higher_better" else -ev.ceiling_value
            why = f"{ev.group.gid}: best measured {mid}@{eff}={raw:g} misses ceiling {ceiling_raw:g}"
            if ev.group.kind() == "independent":
                ceiling_excluded.setdefault(mid, []).append(why)
            else:
                result["flags"].append(f"ceiling (vendor group, not excluding): {why}")
    if ceiling_excluded:
        for mid, whys in ceiling_excluded.items():
            result["excluded_models"].append({"model": mid, "reason": "fails the ceiling at its best measured effort: " + "; ".join(whys)})
        arms = [a for a in arms if a.model not in ceiling_excluded]
        result["candidates"] = [a.label() for a in arms]
        result["flags"].append("ceiling rule applied (assumes quality does not rise as effort falls)")
        if not arms:
            result["status"] = "NO_CANDIDATES"
            return result
        evals = [evaluate_group(g, arms, req, catalog) for g in groups]
        evals = [ev for ev in evals if ev.present]
    for ev in evals:
        for c in ev.group.conflicts:
            result["flags"].append(f"{ev.group.gid}: duplicate row {c}")
        cells = ev.group.cells
        result["groups"].append({
            "group": ev.group.gid,
            "metric_id": ev.group.metric_id,
            "direction": ev.group.direction,
            "source_types": sorted(ev.group.source_types),
            "weight": ev.weight,
            "vendor_chart_ranks_rival_vendor": ev.group.cross_vendor_vendor_chart,
            "publisher_vendor": ev.group.publisher_vendor,
            "unusable": ev.unusable,
            "ceiling_raw": None if ev.ceiling_value is None else (ev.ceiling_value if ev.group.direction == "higher_better" else -ev.ceiling_value),
            "maker_p": (success_probability(ev.group, cells[(maker.model, maker.effort)])
                        if maker and (maker.model, maker.effort) in cells else None),
            "checker_p": (success_probability(ev.group, cells[(ev.choice.model, ev.choice.effort)])
                          if maker and ev.choice else None),
            "choice": ev.choice.label() if ev.choice else None,
            "arms": [
                {"arm": a.label(), "score": cells[(a.model, a.effort)].raw, "cost": cells[(a.model, a.effort)].cost,
                 "p": success_probability(ev.group, cells[(a.model, a.effort)]),
                 "expected_cost_usd": report_cost(expected_cost(ev.group, cells[(a.model, a.effort)], req)),
                 "dominated": a in ev.dominated}
                for a in sorted(ev.present, key=lambda a: -cells[(a.model, a.effort)].score)
            ],
        })
        if maker and ev.group.unit in SUCCESS_UNITS and (maker.model, maker.effort) not in cells:
            result["missing"].append({"model": maker.model, "effort": maker.effort,
                                      "metric": ev.group.metric_id, "group": ev.group.gid, "need": "maker p for side-by-side comparison"})

    usable = [ev for ev in evals if not ev.unusable]
    decisive = [ev for ev in usable if ev.choice is not None]
    for ev in usable:
        for a in ev.unpriced:
            result["missing"].append({"model": a.model, "effort": a.effort, "metric": ev.group.metric_id,
                                      "group": ev.group.gid, "need": "cost usd_per_task"})

    stats: dict[Arm, dict] = {}
    for a in {ev.choice for ev in decisive if ev.choice is not None}:
        containing = [ev for ev in decisive if a in ev.present]
        won = [ev for ev in containing if wins(a, ev)]
        num = sum(ev.weight for ev in won)
        den = sum(ev.weight for ev in containing)
        # Groups that chart one model's efforts choose an effort, not a model: the arm
        # must also win a weighted majority of the cross-model groups it appears in.
        cross = [ev for ev in containing if ev.group.models_present(ev.present) >= 2]
        cross_num = sum(ev.weight for ev in cross if ev in won)
        cross_den = sum(ev.weight for ev in cross)
        stats[a] = {"won": won, "lost": [ev for ev in containing if ev not in won],
                    "num": num, "den": den, "cross_num": cross_num, "cross_den": cross_den,
                    "consistent": (den > 0 and num / den > 0.5
                                   and (cross_den == 0 or cross_num / cross_den > 0.5))}

    consistent = sorted((a for a, s in stats.items() if s["consistent"]),
                        key=lambda a: (-stats[a]["num"], catalog.list_price.get(a.model, float("inf")),
                                       tuple(-x for x in version_key(a.model))))

    def unmeasured(ref: Arm | None) -> None:
        """Candidates with no comparable score; only those no pricier (list) than ref could change it."""
        measured = {a.model for ev in usable for a in ev.present}
        ref_price = catalog.list_price.get(ref.model, float("inf")) if ref else float("inf")
        success_metrics = [mid for mid in req["evidence_metrics"]
                           if catalog.metrics[mid].get("unit_default") in SUCCESS_UNITS]
        if not success_metrics:
            success_metrics = [ev.group.metric_id for ev in usable]
        for model in sorted({a.model for a in arms} - measured):
            if catalog.list_price.get(model, float("inf")) <= ref_price:
                result["missing"].append({"model": model, "effort": None,
                                          "metric": "any of " + ", ".join(success_metrics or req["evidence_metrics"]),
                                          "group": None, "need": "success-rate score and cost at an allowed effort"})

    if len(consistent) == 1:
        a = consistent[0]
        s = stats[a]
        result["status"] = "RECOMMENDED"
        result["recommended"] = arm_dict(a)
        result["deciding_groups"] = [group_ref(ev) for ev in s["won"]]
        result["disagreements"] = [f"{group_ref(ev)}: choice {ev.choice.label()}" for ev in s["lost"]]
        independent = [ev for ev in s["won"] if ev.group.kind() == "independent"]
        cross_model = [ev for ev in independent if ev.group.models_present(ev.present) >= 2]
        if len(cross_model) >= 2 and not s["lost"]:
            result["confidence"] = "high"
        elif cross_model:
            result["confidence"] = "medium"
        else:
            result["confidence"] = "low"
        if independent and not cross_model:
            result["flags"].append("independent deciding groups compare efforts of one model only; the model choice rests on vendor charts")
        if all(ev.group.cross_vendor_vendor_chart for ev in s["won"]):
            result["flags"].append("every deciding group is a vendor chart ranking a rival vendor")
        unmeasured(a)
    else:
        result["status"] = "INSUFFICIENT_EVIDENCE"
        if len(consistent) > 1:
            # Rivals that never meet in a decisive group: the missing cross-coverage decides it.
            for a in consistent:
                result["disagreements"].append(
                    f"{a.label()} wins {stats[a]['num']:g}/{stats[a]['den']:g} weight in "
                    + ", ".join(ev.group.gid for ev in stats[a]["won"]))
            for a in consistent:
                for b in consistent:
                    if a == b:
                        continue
                    for ev in stats[b]["won"]:
                        if a not in ev.present:
                            result["missing"].append({"model": a.model, "effort": a.effort, "metric": ev.group.metric_id,
                                                      "group": ev.group.gid, "need": f"score+cost to compare with {b.label()}"})
        else:
            for a, s in sorted(stats.items(), key=lambda kv: -kv[1]["num"]):
                why = []
                if s["den"] and s["num"] / s["den"] <= 0.5:
                    why.append(f"wins {s['num']:g}/{s['den']:g} weight")
                if s["cross_den"] and s["cross_num"] / s["cross_den"] <= 0.5:
                    why.append(f"wins {s['cross_num']:g}/{s['cross_den']:g} cross-model weight")
                result["disagreements"].append(f"{a.label()}: " + "; ".join(why))
            if not decisive:
                result["flags"].append("no group has a priced success-rate arm")
        unmeasured(None)
    # de-duplicate missing entries, keep order
    seen = set()
    uniq = []
    for m in result["missing"]:
        key = json.dumps(m, sort_keys=True)
        if key not in seen:
            seen.add(key)
            uniq.append(m)
    result["missing"] = uniq
    return result


def current_arm(point: dict, catalog: Catalog) -> Arm | None:
    exp = point.get("expands_to") or {}
    if not exp.get("model"):
        return None
    dispatch = exp["model"]
    canonical = ANTIGRAVITY_MODELS.get(dispatch, (dispatch, None))[0] if exp.get("provider") == "antigravity" else dispatch
    canonical = catalog.resolve(canonical) or canonical
    return Arm(canonical, exp.get("effort", ""), exp.get("provider", ""),
               dispatch if dispatch != canonical else None)


def recommend(catalog: Catalog, reqs: dict, points: dict[str, dict],
              scenario: tuple[float, float, float] | None = None) -> list[dict]:
    makers: dict[str, Arm] = {}
    out: dict[str, dict] = {}
    for op in reqs["order"]:
        if op not in reqs["ops"]:
            continue
        req = dict(reqs["ops"][op])
        if scenario is not None:
            overhead, detection, silent_multiplier = scenario
            req["attempt_overhead_usd"] = overhead
            req["failure_detection_probability"] = (0 if op in {"review.audit", "advisor.deep"} else detection)
            req["silent_failure_cost_usd"] *= silent_multiplier
        deps = {d: makers[d] for d in op_dependencies(req) if d in makers}
        res = decide(op, req, catalog, deps, points[op].get("expands_to", {}))
        for d in op_dependencies(req):
            if d in makers and out[d]["status"] != "RECOMMENDED":
                res["flags"].append(f"{d} is {out[d]['status']}; constraint uses its current expands_to")
        out[op] = res
        rec = res["recommended"]
        maker = Arm(rec.get("catalog_model", rec["model"]), rec["effort"], rec["provider"],
                    rec["model"] if rec.get("catalog_model") else None) if rec else current_arm(points[op], catalog)
        if maker is not None:
            makers[op] = maker
    order = [p for p in points if p in out]
    for r in out.values():
        cur = r["current"]
        rec = r["recommended"]
        r["matches_current"] = bool(rec) and all(rec[k] == cur.get(k) for k in ("provider", "model", "effort"))
    return [out[p] for p in order]


ROBUSTNESS_OVERHEAD = (0.1, 0.5, 1.0, 2.0)
ROBUSTNESS_DETECTION = (0.5, 0.75, 0.95)
ROBUSTNESS_SILENT_MULTIPLIER = (0.5, 1.0, 2.0)


def outcome(result: dict) -> str:
    rec = result["recommended"]
    return f"{rec['provider']}/{rec['model']}/{rec['effort']}" if rec else result["status"]


def assess_robustness(catalog: Catalog, reqs: dict, points: dict[str, dict], results: list[dict]) -> None:
    """Re-run the complete dependency graph on the assumption grid and locate one flip."""
    axes = (ROBUSTNESS_OVERHEAD, ROBUSTNESS_DETECTION, ROBUSTNESS_SILENT_MULTIPLIER)
    scenarios = list(itertools.product(*axes))
    cache = {scenario: {r["op"]: outcome(r) for r in recommend(catalog, reqs, points, scenario)}
             for scenario in scenarios}
    base = (0.5, 0.75, 1.0)

    for result in results:
        op = result["op"]
        outcomes = sorted({cache[s][op] for s in scenarios})
        if len(outcomes) == 1 and result["recommended"]:
            result["robustness"] = {"status": "CLEAR", "outcomes": outcomes, "threshold": None}
            continue
        if len(outcomes) == 1:
            result["robustness"] = {"status": "ASSUMPTION_SENSITIVE", "outcomes": outcomes,
                                    "threshold": "No winning arm anywhere in the grid; evidence remains insufficient."}
            continue

        edges = []
        for axis, values in enumerate(axes):
            for low, high in zip(values, values[1:]):
                for other in itertools.product(*(axes[i] for i in range(3) if i != axis)):
                    scenario_low = list(other)
                    scenario_low.insert(axis, low)
                    scenario_high = list(scenario_low)
                    scenario_high[axis] = high
                    a, b = tuple(scenario_low), tuple(scenario_high)
                    if cache[a][op] != cache[b][op]:
                        distance = sum(abs((a[i] + b[i]) / 2 - base[i]) / (axes[i][-1] - axes[i][0])
                                       for i in range(3))
                        edges.append((distance, axis, a, b))
        _, axis, left, right = min(edges)
        left_value, right_value = left[axis], right[axis]
        left_outcome, right_outcome = cache[left][op], cache[right][op]
        for _ in range(16):
            mid = (left_value + right_value) / 2
            probe = list(left)
            probe[axis] = mid
            probe_outcome = next(r for r in recommend(catalog, reqs, points, tuple(probe)) if r["op"] == op)
            if outcome(probe_outcome) == left_outcome:
                left_value = mid
            else:
                right_value = mid
        threshold = (left_value + right_value) / 2
        silent_base = reqs["ops"][op]["silent_failure_cost_usd"]
        held_detection = 0 if op in {"review.audit", "advisor.deep"} else left[1]
        if axis == 0:
            assumption = f"overhead ≈ ${threshold:.3g} (d={held_detection:g}, silent cost=${silent_base * left[2]:g})"
        elif axis == 1:
            assumption = f"d ≈ {threshold:.3g} (overhead=${left[0]:g}, silent cost=${silent_base * left[2]:g})"
        else:
            assumption = f"silent cost ≈ ${silent_base * threshold:.3g} (overhead=${left[0]:g}, d={held_detection:g})"
        result["robustness"] = {"status": "ASSUMPTION_SENSITIVE", "outcomes": outcomes,
                                "threshold": f"{assumption}: {left_outcome} → {right_outcome}"}


# --------------------------------------------------------------- output


def _cell(items: list[str], limit: int = 3) -> str:
    if not items:
        return "—"
    shown = items[:limit]
    more = f" (+{len(items) - limit} more)" if len(items) > limit else ""
    return "<br>".join(s.replace("|", "\\|") for s in shown) + more


def render_markdown(results: list[dict]) -> str:
    lines = [
        "| op | current expands_to | recommended arm | confidence | robustness / flip threshold | deciding groups | disagreements | missing |",
        "|----|--------------------|-----------------|------------|-----------------------------|-----------------|---------------|---------|",
    ]
    for r in results:
        cur = r["current"]
        cur_s = f"{cur.get('provider')}/{cur.get('model')}/{cur.get('effort')}"
        if r["recommended"]:
            rec = r["recommended"]
            rec_s = f"**{rec['provider']}/{rec['model']}/{rec['effort']}**" + (" (= current)" if r["matches_current"] else "")
        else:
            rec_s = r["status"]
        missing = list(dict.fromkeys(
            f"{m['model']}{'@' + m['effort'] if m.get('effort') else ''} × {m['metric']} ({m['need']})" for m in r["missing"]))
        robust = r.get("robustness", {})
        robustness = robust.get("status", "—")
        if robust.get("threshold"):
            robustness += "<br>" + robust["threshold"]
        lines.append(
            f"| `{r['op']}` | {cur_s} | {rec_s} | {r['confidence'] or '—'} "
            f"| {robustness} | {_cell(r['deciding_groups'])} | {_cell(r['disagreements'])} | {_cell(missing)} |"
        )
    lines.append("")
    for r in results:
        lines.append(f"### `{r['op']}` — {r['status']}")
        lines.append("")
        lines.append(f"- candidates ({len(r['candidates'])}): {', '.join(r['candidates']) or 'none'}")
        if r.get("robustness"):
            lines.append(f"- robustness: {r['robustness']['status']} — {r['robustness']['threshold'] or 'same arm across the grid'}")
        if r["excluded_models"]:
            lines.append("- excluded: " + "; ".join(f"{e['model']} ({e['reason']})" for e in r["excluded_models"]))
        for f in r["flags"]:
            lines.append(f"- flag: {f}")
        for g in r["groups"]:
            arms = ", ".join(
                f"{a['arm']}={a['score']:g}" + (f" p={a['p']:.3g}" if a["p"] is not None else " p=—")
                + (f" cost=${a['cost']:g}" if a["cost"] is not None else " cost=?")
                + (" E=∞" if a["expected_cost_usd"] == "Infinity" else
                   f" E=${a['expected_cost_usd']:.3g}" if a["expected_cost_usd"] is not None else " E=—")
                + (" dominated" if a["dominated"] else "") for a in g["arms"])
            status = f"unusable: {g['unusable']}" if g["unusable"] else f"choice {g['choice'] or 'none (no priced success-rate arm)'}"
            ceiling = "" if g["ceiling_raw"] is None else f", ceiling {g['ceiling_raw']:.4g}"
            flag = ", vendor chart ranks rival vendor" if g["vendor_chart_ranks_rival_vendor"] else ""
            maker = (f", maker p={g['maker_p']:.3g}, checker p={g['checker_p']:.3g}"
                     if g["maker_p"] is not None and g["checker_p"] is not None else "")
            lines.append(f"  - `{g['group']}` ({g['metric_id']}, {'/'.join(g['source_types'])}, w={g['weight']:g}{flag}{ceiling}{maker}): {status} — {arms}")
        if r["missing"]:
            lines.append("- missing:")
            for m in r["missing"]:
                eff = f"@{m['effort']}" if m.get("effort") else ""
                grp = f" [{m['group']}]" if m.get("group") else ""
                lines.append(f"  - {m['model']}{eff} × {m['metric']}{grp}: {m['need']}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    ap.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    ap.add_argument("--requirements", type=Path, help="default: <policy>/op-requirements.json")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--op", action="append", help="limit output to these op ids")
    args = ap.parse_args(argv)

    catalog = Catalog.from_dir(args.catalog)
    points_doc = _load(args.policy / "operating-points.json")
    points = {p["id"]: p for p in points_doc["operating_points"]}
    try:
        reqs = load_requirements(args.requirements or args.policy / "op-requirements.json", catalog, set(points))
    except RequirementsError as e:
        print(f"op-requirements invalid:\n{e}", file=sys.stderr)
        return 2
    results = recommend(catalog, reqs, points)
    assess_robustness(catalog, reqs, points, results)
    if args.op:
        results = [r for r in results if r["op"] in args.op]
    if args.json:
        print(json.dumps({"catalog_ref": reqs["doc"].get("catalog_ref"), "requirements_status": reqs["doc"].get("status"),
                          "results": results}, indent=2))
    else:
        print(render_markdown(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
