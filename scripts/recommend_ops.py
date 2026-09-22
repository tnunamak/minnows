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
import json
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
}
PROVIDER_TO_LANE = {vendor: lane for lane, (vendor, _) in LANES.items()}
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
ABOVE_DEFAULT_EFFORTS = frozenset({"xhigh", "max", "ultra"})
GRADE_RANK = {"A": 3, "B": 2, "C": 1, "D": 0}
INDEPENDENT_SOURCE_TYPES = frozenset({"third_party_board", "third_party_eval", "local_eval"})
VENDOR_SOURCE_TYPES = frozenset({"vendor_table", "digitized_chart"})
BAR_TYPES = frozenset({"frontier_best", "within_points_of_best", "at_least_op"})
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


def version_key(model_id: str) -> tuple[int, ...]:
    """Numeric generation key parsed from a canonical id (claude-opus-5-5 -> (5, 5)).

    The catalog has no release-date field; this is used only to pick the
    newest GA model within one (provider, tier).
    """
    return tuple(int(x) for x in re.findall(r"\d+", model_id))


@dataclass
class Catalog:
    models: dict[str, dict]
    aliases: dict[str, str]
    metrics: dict[str, dict]
    surfaces: dict[tuple[str, str], list[str]]
    rows: list[dict]
    list_price: dict[str, float]
    publishers: dict[str, str]

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
    errs: list[str] = []
    ops: dict[str, dict] = {}
    for raw in doc.get("ops", []):
        op = raw.get("op")
        req = {k: v for k, v in defaults.items() if k != "provider_restrictions"}
        req.update(raw)
        where = f"op {op!r}"
        if op not in op_ids:
            errs.append(f"{where}: not an operating-points.json id")
        if op in ops:
            errs.append(f"{where}: duplicate entry")
        for m in req.get("evidence_metrics") or []:
            if m not in catalog.metrics:
                errs.append(f"{where}: metric {m!r} not in metrics.json")
        if not req.get("evidence_metrics"):
            errs.append(f"{where}: evidence_metrics must be non-empty")
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
        bar = req.get("bar") or {}
        if bar.get("type") not in BAR_TYPES:
            errs.append(f"{where}: bar.type must be one of {sorted(BAR_TYPES)}")
        if bar.get("type") == "within_points_of_best" and not isinstance(bar.get("points"), (int, float)):
            errs.append(f"{where}: within_points_of_best needs numeric points")
        if bar.get("type") == "at_least_op" and bar.get("op") not in op_ids:
            errs.append(f"{where}: at_least_op references unknown op {bar.get('op')!r}")
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
    if req["bar"]["type"] == "at_least_op":
        deps.append(req["bar"]["op"])
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

    def label(self) -> str:
        return f"{self.lane}/{self.model}/{self.effort}"


def arm_dict(arm: Arm | None) -> dict | None:
    return None if arm is None else {"provider": arm.lane, "model": arm.model, "effort": arm.effort}


def candidate_arms(req: dict, catalog: Catalog, makers: dict[str, Arm]) -> tuple[list[Arm], list[dict], list[str]]:
    """Step 1: GA + tier + allowed lane + newest in tier + effort valid on the lane's CLI surface."""
    arms: list[Arm] = []
    excluded: list[dict] = []
    notes: list[str] = []
    allowed_lanes = req["allowed_providers"]
    allowed_vendors = {LANES[p][0] for p in allowed_lanes}

    newest: dict[tuple[str, str], str] = {}
    for m in catalog.models.values():
        if m.get("status") == "ga" and m.get("tier"):
            key = (m["provider"], m["tier"])
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
        if m["provider"] not in allowed_vendors:
            continue
        mid = m["id"]
        if m.get("status") != "ga":
            continue
        if not m.get("tier"):
            excluded.append({"model": mid, "reason": "no tier in models.json"})
            continue
        if newest[(m["provider"], m["tier"])] != mid:
            excluded.append({"model": mid, "reason": f"superseded in tier {m['tier']!r} by {newest[(m['provider'], m['tier'])]}"})
            continue
        hit = next((c for key, value, c in forbidden if m.get(key) == value), None)
        if hit:
            excluded.append({"model": mid, "reason": f"constraint {hit}"})
            continue
        lane = PROVIDER_TO_LANE[m["provider"]]
        surface = LANES[lane][1]
        efforts = catalog.surfaces.get((mid, surface))
        if efforts is None:
            efforts = catalog.surfaces.get((mid, "api"))
            if efforts is None:
                excluded.append({"model": mid, "reason": f"no {surface} or api effort surface"})
                continue
            notes.append(f"{mid}: no {surface} surface; efforts taken from the api surface")
        usable = [e for e in req["allowed_efforts"] if e in efforts]
        if not usable:
            excluded.append({"model": mid, "reason": f"no allowed effort valid on its surface ({efforts})"})
            continue
        arms.extend(Arm(mid, e, lane) for e in usable)
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

    @property
    def scale(self) -> float:
        """Points are percentage points when every value in the group is a 0-1 fraction."""
        if self.cells and all(abs(c.raw) <= 1.0 for c in self.cells.values()):
            return 100.0
        return 1.0

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
            if effort not in EFFORTS:
                continue  # effort unattributed (None/unknown/adaptive): cannot map to an arm
            cost = r.get("cost") if isinstance(r.get("cost"), dict) else None
            cost_v = cost["value"] if cost and cost.get("unit") == req["cost_basis"] else None
            raw_score = float(r["score"])
            cell = Cell(raw_score if direction == "higher_better" else -raw_score, raw_score, cost_v, r)
            key = (mid, effort)
            prev = g.cells.get(key)
            if prev is None or r.get("observed_at", "") > prev.row.get("observed_at", ""):
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
    bar_value: float | None  # normalized score an arm must reach
    eligible: list[Arm]
    dominated: list[Arm]
    choice: Arm | None
    unpriced_eligible: list[Arm]
    unusable: str | None = None


def arm_sort_key(arm: Arm, cost: float, catalog: Catalog) -> tuple:
    """Step 3 tie-break: cost per task -> cheaper list price (tier) -> newer model."""
    price = catalog.list_price.get(arm.model, float("inf"))
    return (cost, price, tuple(-x for x in version_key(arm.model)), EFFORTS.index(arm.effort))


def evaluate_group(g: Group, arms: list[Arm], req: dict, catalog: Catalog, maker: Arm | None) -> GroupEval:
    """Step 2-3: present arms, domination, bar, eligibility, cheapest eligible."""
    present = [a for a in arms if (a.model, a.effort) in g.cells]
    ev = GroupEval(g, g.weight(req), present, None, [], [], None, [])
    if not present:
        return ev
    bar = req["bar"]
    if bar["type"] != "at_least_op" and len(present) < 2:
        ev.unusable = "one candidate arm only; nothing to compare"
        return ev
    if bar["type"] == "at_least_op":
        if maker is None:
            ev.unusable = "maker op has no arm"
            return ev
        cell = g.cells.get((maker.model, maker.effort))
        if cell is None:
            ev.unusable = f"maker arm {maker.model}@{maker.effort} not in group"
            return ev
        ev.bar_value = cell.score
    else:
        best = max(g.cells[(a.model, a.effort)].score for a in present)
        slack = bar.get("points", 0) / g.scale if bar["type"] == "within_points_of_best" else 0.0
        ev.bar_value = best - slack
    cell_of = {a: g.cells[(a.model, a.effort)] for a in present}
    ev.eligible = [a for a in present if cell_of[a].score >= ev.bar_value - EPS]
    cost_of = {a: c.cost for a, c in cell_of.items() if c.cost is not None}
    for a, cost in cost_of.items():
        score = cell_of[a].score
        if any(
            cell_of[b].score >= score and cost_of[b] <= cost and (cell_of[b].score > score or cost_of[b] < cost)
            for b in cost_of if b != a
        ):
            ev.dominated.append(a)
    pool = [a for a in ev.eligible if a in cost_of and a not in ev.dominated]
    if pool:
        ev.choice = min(pool, key=lambda a: arm_sort_key(a, cost_of[a], catalog))
    # An eligible arm without a per-task cost might be cheaper than the choice: report it.
    ev.unpriced_eligible = [a for a in ev.eligible if cell_of[a].cost is None]
    return ev


def wins(arm: Arm, ev: GroupEval) -> bool:
    """The arm is the group's choice, or eligible and no more expensive than it."""
    if ev.choice is None or arm not in ev.present:
        return False
    if arm == ev.choice:
        return True
    cost = ev.group.cells[(arm.model, arm.effort)].cost
    choice_cost = ev.group.cells[(ev.choice.model, ev.choice.effort)].cost
    return arm in ev.eligible and cost is not None and choice_cost is not None and cost <= choice_cost + EPS


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
    maker = None
    if req["bar"]["type"] == "at_least_op":
        maker = makers.get(req["bar"]["op"])
        result["flags"].append(f"bar at_least_op {req['bar']['op']}: maker arm {maker.label() if maker else 'none'}")
    if not arms:
        result["status"] = "NO_CANDIDATES"
        return result

    groups, skipped = build_groups(req, catalog)
    result["skipped_groups"] = skipped
    evals = [evaluate_group(g, arms, req, catalog, maker) for g in groups]
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
            "bar_raw": None if ev.bar_value is None else (ev.bar_value if ev.group.direction == "higher_better" else -ev.bar_value),
            "choice": ev.choice.label() if ev.choice else None,
            "arms": [
                {"arm": a.label(), "score": cells[(a.model, a.effort)].raw, "cost": cells[(a.model, a.effort)].cost,
                 "eligible": None if ev.unusable else a in ev.eligible, "dominated": a in ev.dominated}
                for a in sorted(ev.present, key=lambda a: -cells[(a.model, a.effort)].score)
            ],
        })
        if ev.unusable and maker is not None and ev.unusable.startswith("maker arm"):
            result["missing"].append({"model": maker.model if maker else None, "effort": maker.effort if maker else None,
                                      "metric": ev.group.metric_id, "group": ev.group.gid, "need": "maker score"})

    usable = [ev for ev in evals if not ev.unusable]
    decisive = [ev for ev in usable if ev.choice is not None]
    for ev in usable:
        for a in ev.unpriced_eligible:
            result["missing"].append({"model": a.model, "effort": a.effort, "metric": ev.group.metric_id,
                                      "group": ev.group.gid, "need": "cost usd_per_task"})

    stats: dict[Arm, dict] = {}
    for a in {ev.choice for ev in decisive if ev.choice is not None}:
        containing = [ev for ev in decisive if a in ev.present]
        won = [ev for ev in containing if wins(a, ev)]
        failed = [ev for ev in usable if a in ev.present and a not in ev.eligible]
        num = sum(ev.weight for ev in won)
        den = sum(ev.weight for ev in containing)
        # Groups that chart one model's efforts choose an effort, not a model: the arm
        # must also win a weighted majority of the cross-model groups it appears in.
        cross = [ev for ev in containing if ev.group.models_present(ev.present) >= 2]
        cross_num = sum(ev.weight for ev in cross if ev in won)
        cross_den = sum(ev.weight for ev in cross)
        stats[a] = {"won": won, "lost": [ev for ev in containing if ev not in won], "failed": failed,
                    "num": num, "den": den, "cross_num": cross_num, "cross_den": cross_den,
                    "consistent": (den > 0 and num / den > 0.5 and not failed
                                   and (cross_den == 0 or cross_num / cross_den > 0.5))}

    consistent = sorted((a for a, s in stats.items() if s["consistent"]),
                        key=lambda a: (-stats[a]["num"], catalog.list_price.get(a.model, float("inf")),
                                       tuple(-x for x in version_key(a.model))))

    def unmeasured(ref: Arm | None) -> None:
        """Candidates with no comparable score; only those no pricier (list) than ref could change it."""
        measured = {a.model for ev in usable for a in ev.present}
        ref_price = catalog.list_price.get(ref.model, float("inf")) if ref else float("inf")
        for model in sorted({a.model for a in arms} - measured):
            if catalog.list_price.get(model, float("inf")) <= ref_price:
                result["missing"].append({"model": model, "effort": None, "metric": "any of " + ", ".join(req["evidence_metrics"]),
                                          "group": None, "need": "any score at an allowed effort"})

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
                if s["failed"]:
                    why.append("fails bar in " + ", ".join(ev.group.gid for ev in s["failed"]))
                result["disagreements"].append(f"{a.label()}: " + "; ".join(why))
                for ev in s["failed"] + s["lost"]:
                    here = ("fails the bar" if ev in s["failed"] else
                            f"loses to {ev.choice.label() if ev.choice else 'no arm'}")
                    result["missing"].append({"model": a.model, "effort": a.effort, "metric": ev.group.metric_id,
                                              "group": ev.group.gid,
                                              "need": f"a second comparable score+cost ({ev.group.kind()} group: {here})"})
            if not decisive:
                result["flags"].append("no group has a priced eligible arm")
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


def current_arm(point: dict) -> Arm | None:
    exp = point.get("expands_to") or {}
    if not exp.get("model"):
        return None
    return Arm(exp["model"], exp.get("effort", ""), exp.get("provider", ""))


def recommend(catalog: Catalog, reqs: dict, points: dict[str, dict]) -> list[dict]:
    makers: dict[str, Arm] = {}
    out: dict[str, dict] = {}
    for op in reqs["order"]:
        if op not in reqs["ops"]:
            continue
        req = reqs["ops"][op]
        deps = {d: makers[d] for d in op_dependencies(req) if d in makers}
        res = decide(op, req, catalog, deps, points[op].get("expands_to", {}))
        for d in op_dependencies(req):
            if d in makers and out[d]["status"] != "RECOMMENDED":
                res["flags"].append(f"{d} is {out[d]['status']}; constraint uses its current expands_to")
        out[op] = res
        rec = res["recommended"]
        maker = Arm(rec["model"], rec["effort"], rec["provider"]) if rec else current_arm(points[op])
        if maker is not None:
            makers[op] = maker
    order = [p for p in points if p in out]
    for r in out.values():
        cur = r["current"]
        rec = r["recommended"]
        r["matches_current"] = bool(rec) and all(rec[k] == cur.get(k) for k in ("provider", "model", "effort"))
    return [out[p] for p in order]


# --------------------------------------------------------------- output


def _cell(items: list[str], limit: int = 3) -> str:
    if not items:
        return "—"
    shown = items[:limit]
    more = f" (+{len(items) - limit} more)" if len(items) > limit else ""
    return "<br>".join(s.replace("|", "\\|") for s in shown) + more


def render_markdown(results: list[dict]) -> str:
    lines = [
        "| op | current expands_to | recommended arm | confidence | deciding groups | disagreements | missing |",
        "|----|--------------------|-----------------|------------|-----------------|---------------|---------|",
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
        lines.append(
            f"| `{r['op']}` | {cur_s} | {rec_s} | {r['confidence'] or '—'} | {_cell(r['deciding_groups'])} "
            f"| {_cell(r['disagreements'])} | {_cell(missing)} |"
        )
    lines.append("")
    for r in results:
        lines.append(f"### `{r['op']}` — {r['status']}")
        lines.append("")
        lines.append(f"- candidates ({len(r['candidates'])}): {', '.join(r['candidates']) or 'none'}")
        if r["excluded_models"]:
            lines.append("- excluded: " + "; ".join(f"{e['model']} ({e['reason']})" for e in r["excluded_models"]))
        for f in r["flags"]:
            lines.append(f"- flag: {f}")
        for g in r["groups"]:
            arms = ", ".join(
                f"{a['arm']}={a['score']:g}" + (f" ${a['cost']:g}" if a["cost"] is not None else " $?")
                + (" ✗bar" if a["eligible"] is False else "") + (" dominated" if a["dominated"] else "")
                for a in g["arms"])
            status = f"unusable: {g['unusable']}" if g["unusable"] else f"choice {g['choice'] or 'none (no priced eligible arm)'}"
            bar = "" if g["bar_raw"] is None else f", bar {g['bar_raw']:.4g}"
            flag = ", vendor chart ranks rival vendor" if g["vendor_chart_ranks_rival_vendor"] else ""
            lines.append(f"  - `{g['group']}` ({g['metric_id']}, {'/'.join(g['source_types'])}, w={g['weight']:g}{flag}{bar}): {status} — {arms}")
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
