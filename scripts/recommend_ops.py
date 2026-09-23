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
import datetime as dt
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
}
GROK_LIVE_MODELS = frozenset({"grok-4.7"})  # `grok models`, verified 2026-09-23.
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


def generation_key(model: dict) -> tuple:
    """Prefer comparable release dates; caller falls back when either date is absent."""
    return (model.get("released") or "", version_key(model["id"]))


def newer(left: dict, right: dict) -> bool:
    if left.get("released") and right.get("released"):
        return generation_key(left) > generation_key(right)
    return version_key(left["id"]) > version_key(right["id"])


@dataclass
class Catalog:
    models: dict[str, dict]
    aliases: dict[str, str]
    metrics: dict[str, dict]
    surfaces: dict[tuple[str, str], list[str]]
    rows: list[dict]
    list_price: dict[str, float]
    publishers: dict[str, str]
    prices: dict[str, list[dict]] = field(default_factory=dict)

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
        prices: dict[str, list[dict]] = {}
        agent_vendor = {"claude-code": "anthropic", "codex": "openai", "grok": "xai",
                        "google": "google", "qwen-code": "alibaba"}
        for path in sorted((root / "pricing").glob("*.json")):
            doc = _load(path)
            if doc.get("kind") != "api_usd":
                continue
            for mid, rate in doc.get("models", {}).items():
                if isinstance(rate.get("output_per_m"), (int, float)):
                    direct = agent_vendor.get(doc.get("agent")) == models.get(mid, {}).get("provider")
                    prices.setdefault(mid, []).append({**rate, "_file": path.name,
                                                        "_direct": direct, "_retrieved": doc.get("retrieved_at", "")})
                    future = rate.get("post_valid_until")
                    if future and rate.get("valid_until"):
                        next_day = (dt.date.fromisoformat(rate["valid_until"]) + dt.timedelta(days=1)).isoformat()
                        prices[mid].append({**future, "valid_from": next_day, "_file": path.name,
                                            "_direct": direct, "_retrieved": doc.get("retrieved_at", "")})
        today = dt.date.today().isoformat()
        for mid, entries in prices.items():
            active = [p for p in entries if p.get("valid_from", "") <= today <= p.get("valid_until", "9999-12-31")]
            if active:
                chosen = max(active, key=lambda p: (p["_direct"], p.get("valid_from", ""), p["_retrieved"], p["_file"]))
                list_price[mid] = float(chosen["output_per_m"])
        publishers = {}
        src_path = root / "SOURCES.json"
        if src_path.is_file():
            publishers = {s["id"]: s.get("publisher", "") for s in _load(src_path)["sources"]}
        return cls(models, aliases, metrics, surfaces, rows, list_price, publishers, prices)

    def price_at(self, model: str, date: dt.date) -> dict | None:
        entries = [p for p in self.prices.get(model, [])
                   if p.get("valid_from", "") <= date.isoformat() <= p.get("valid_until", "9999-12-31")]
        return max(entries, key=lambda p: (p["_direct"], p.get("valid_from", ""),
                                           p.get("_retrieved", ""), p["_file"])) if entries else None

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
                               ("xai", "xai"), ("google", "google"), ("deepseek", "deepseek"),
                               ("z.ai", "zai"), ("moonshot", "moonshot"),
                               ("minimax", "minimax"), ("mistral", "mistral")):
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
        if req.get("policy_horizon_days", 90) < 1:
            errs.append(f"{where}: policy_horizon_days must be positive")
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


def dispatches_for_lane(lane: str, model: dict, catalog: Catalog | None = None) -> list[tuple[str, str | None]]:
    """Return dispatch ids and fixed efforts for a catalog model on one lane."""
    if LANES[lane][0] == model["provider"]:
        return [(model["id"], None)]
    if lane == "antigravity":
        return [(slug, effort) for slug, (mid, effort) in ANTIGRAVITY_MODELS.items()
                if (catalog.resolve(mid) if catalog else mid) == model["id"]]
    return []


def candidate_arms(req: dict, catalog: Catalog, makers: dict[str, Arm]) -> tuple[list[Arm], list[dict], list[str]]:
    """Step 1: GA + tier + reachable lane + newest on that lane + valid CLI effort."""
    arms: list[Arm] = []
    excluded: list[dict] = []
    notes: list[str] = []
    allowed_lanes = req["allowed_providers"]

    newest: dict[tuple[str, ...], str] = {}
    for m in catalog.models.values():
        if m.get("status") == "ga" and m.get("tier") and m.get("access") != "restricted":
            for lane in (allowed_lanes if req.get("lane_scoped_freshness", False) else LANES):
                if dispatches_for_lane(lane, m, catalog):
                    key = (lane, m["provider"], m["tier"]) if req.get("lane_scoped_freshness", False) else (m["provider"], m["tier"])
                    if key not in newest or newer(m, catalog.models[newest[key]]):
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
        reachable = [(lane, dispatches_for_lane(lane, m, catalog)) for lane in allowed_lanes]
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
            freshness_key = (lane, m["provider"], m["tier"]) if req.get("lane_scoped_freshness", False) else (m["provider"], m["tier"])
            if newest[freshness_key] != mid:
                excluded.append({"model": mid, "lane": lane, "reason":
                                 f"superseded in tier {m['tier']!r} by {newest[freshness_key]}"})
                continue
            surface = LANES[lane][1]
            for dispatch, fixed_effort in dispatches:
                if lane == "antigravity" and fixed_effort is None:
                    effort = "medium" if "medium" in req["allowed_efforts"] else req["allowed_efforts"][0]
                    arms.append(Arm(mid, effort, lane, dispatch))
                    continue
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
    available = {(a.model, a.lane) for a in arms}
    excluded = [e for e in excluded if not (
        e.get("reason", "").startswith("no allowed effort") and (e["model"], e.get("lane")) in available)]
    excluded = list({json.dumps(e, sort_keys=True): e for e in excluded}.values())
    return arms, excluded, notes


# ------------------------------------------------------------- groups


@dataclass
class Cell:
    score: float  # normalized: higher is better
    raw: float
    cost: float | None
    row: dict
    ci_lo: float | None = None
    ci_hi: float | None = None
    n: int | None = None
    price_note: str | None = None


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
            price_note = None
            if cost_v is not None:
                start = dt.date.fromisoformat(req.get("price_as_of", dt.date.today().isoformat()))
                end = start + dt.timedelta(days=req.get("policy_horizon_days", 90))
                current_rate, horizon_rate = catalog.price_at(mid, start), catalog.price_at(mid, end)
                if current_rate and current_rate.get("valid_until", "9999-12-31") < end.isoformat():
                    if horizon_rate:
                        keys = ("fresh_input_per_m", "cache_read_per_m", "cache_write_per_m", "output_per_m")
                        ratios = [horizon_rate[k] / current_rate[k] for k in keys
                                  if k in horizon_rate and k in current_rate and current_rate[k] > 0]
                        complete = all(k in horizon_rate and k in current_rate for k in ("fresh_input_per_m", "output_per_m"))
                        price_note = (f"price expires {current_rate['valid_until']}; current input/output "
                                      f"${current_rate.get('fresh_input_per_m', 0):g}/${current_rate['output_per_m']:g}, "
                                      f"horizon ${horizon_rate.get('fresh_input_per_m', 0):g}/${horizon_rate['output_per_m']:g} per million tokens")
                        if complete and ratios and max(ratios) - min(ratios) < EPS:
                            price_note += f"; task cost ${cost_v:g} → ${cost_v * ratios[0]:g}"
                            cost_v *= ratios[0]
                        else:
                            price_note += "; horizon task cost unknown without token mix"
                            cost_v = None
                    else:
                        price_note = f"price expires {current_rate['valid_until']}; horizon price unknown"
                        cost_v = None
            raw_score = float(r["score"])
            n = r.get("n")
            if not isinstance(n, int) or n <= 0:
                match = re.search(r"over (\d+) tasks", r.get("caveat", ""))
                n = int(match.group(1)) if match else None
            ci_lo, ci_hi = r.get("ci_lo"), r.get("ci_hi")
            if (ci_lo is None or ci_hi is None) and n and 0 <= raw_score <= 1 and r["unit"] == "accuracy":
                # Wilson interval when the source supplies n but no interval.
                z = 1.96
                center = (raw_score + z*z/(2*n)) / (1 + z*z/n)
                half = z * math.sqrt(raw_score*(1-raw_score)/n + z*z/(4*n*n)) / (1 + z*z/n)
                ci_lo, ci_hi = max(0, center-half), min(1, center+half)
            cell = Cell(raw_score if direction == "higher_better" else -raw_score, raw_score,
                        cost_v, r, ci_lo, ci_hi, n, price_note)
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
    costs: dict[Arm, float] = field(default_factory=dict)
    bounds: dict[Arm, tuple[float, float]] = field(default_factory=dict)
    ties: list[Arm] = field(default_factory=list)


def success_probability(g: Group, cell: Cell) -> float | None:
    """Convert fractional success and error rates to a success probability."""
    if g.unit not in SUCCESS_UNITS or not 0 <= cell.raw <= 1:
        return None
    return cell.raw if g.direction == "higher_better" else 1 - cell.raw


def score_interval(g: Group, cell: Cell) -> tuple[float, float]:
    if cell.ci_lo is None or cell.ci_hi is None:
        return cell.score, cell.score
    return ((cell.ci_lo, cell.ci_hi) if g.direction == "higher_better"
            else (-cell.ci_hi, -cell.ci_lo))


def expected_cost(g: Group, cell: Cell, req: dict, next_cost: float | None = None,
                  p_override: float | None = None) -> float | None:
    base_p = success_probability(g, cell)
    if base_p is None or cell.cost is None:
        return None
    p = p_override if p_override is not None else base_p
    d = req["failure_detection_probability"]
    c = cell.cost + req["attempt_overhead_usd"]
    s = req["silent_failure_cost_usd"]
    if next_cost is not None:
        return c + (1-p) * (d * next_cost + (1-d) * s)
    pass_k = cell.row.get("pass_at_k")
    if isinstance(pass_k, dict) and isinstance(pass_k.get("k"), int) and pass_k["k"] > 1 and base_p < 1:
        observed = pass_k.get("value")
        if isinstance(observed, (int, float)) and base_p <= observed <= 1:
            retry_p = 1 - ((1-observed)/(1-base_p)) ** (1/(pass_k["k"]-1))
            terminal = 1 - (1-retry_p)*d
            if terminal == 0:
                return float("inf")
            retry_cost = (c + (1-retry_p)*(1-d)*s) / terminal
            return c + (1-p)*(d*retry_cost + (1-d)*s)
    # No same-arm retry evidence: detection does not imply independent success.
    return c + (1-p)*s


def report_cost(value: float | None) -> float | str | None:
    return "Infinity" if value == float("inf") else value


def horizon_price(catalog: Catalog, req: dict, model: str) -> float:
    start = dt.date.fromisoformat(req.get("price_as_of", dt.date.today().isoformat()))
    at_horizon = catalog.price_at(model, start + dt.timedelta(days=req.get("policy_horizon_days", 90)))
    return float(at_horizon["output_per_m"]) if at_horizon else float("inf")


def arm_sort_key(arm: Arm, cost: float, catalog: Catalog, req: dict) -> tuple:
    """Rank by E, then list price, model generation, and effort."""
    price = horizon_price(catalog, req, arm.model)
    return (cost, price, tuple(-x for x in version_key(arm.model)), EFFORTS.index(arm.effort))


def evaluate_group(g: Group, arms: list[Arm], req: dict, catalog: Catalog) -> GroupEval:
    """Compare expected costs inside one group; retain a score ceiling for unmeasured efforts."""
    present = [a for a in arms if (a.model, a.effort) in g.cells]
    if g.kind() == "vendor":
        # Publisher charts can choose effort among the publisher's models; their
        # rival columns are context, never cross-vendor routing evidence.
        present = [a for a in present if g.publisher_vendor and
                   catalog.models[a.model]["provider"] == g.publisher_vendor]
    ev = GroupEval(g, g.weight(req), present, None, [], None, [])
    if not present:
        return ev
    if len({(a.model, a.effort) for a in present}) < 2:
        ev.unusable = "one candidate arm only; nothing to compare"
        return ev
    cell_of = {a: g.cells[(a.model, a.effort)] for a in present}
    if g.unit not in SUCCESS_UNITS or any(success_probability(g, c) is None for c in cell_of.values()):
        ev.unusable = "non-success-rate metric"
        return ev
    ev.ceiling_value = max(c.score for c in cell_of.values())
    ev.unpriced = [a for a in present if cell_of[a].cost is None]
    pool = [a for a in present if a not in ev.unpriced]
    # A detected failure moves to a higher-scoring measured arm. Compute from the
    # top of that ladder, so every fallback has already been priced.
    for a in sorted(pool, key=lambda a: (-cell_of[a].score, a.label())):
        stronger = [b for b in ev.costs if cell_of[b].score > cell_of[a].score + EPS]
        next_arm = min(stronger, key=lambda b: ev.costs[b]) if stronger else None
        cell = cell_of[a]
        p_lo = cell.ci_lo if cell.ci_lo is not None else success_probability(g, cell)
        p_hi = cell.ci_hi if cell.ci_hi is not None else success_probability(g, cell)
        if g.direction == "lower_better":
            p_lo, p_hi = 1-p_hi, 1-p_lo
        ev.costs[a] = expected_cost(g, cell, req, ev.costs.get(next_arm))
        ev.bounds[a] = (
            expected_cost(g, cell, req, ev.bounds[next_arm][0] if next_arm else None, p_hi),
            expected_cost(g, cell, req, ev.bounds[next_arm][1] if next_arm else None, p_lo),
        )
    pool = [a for a in pool if ev.costs[a] != float("inf")]
    if pool:
        best = min(pool, key=lambda a: ev.costs[a])
        ev.ties = [a for a in pool if ev.bounds[a][0] <= ev.bounds[best][1] + EPS
                   and ev.bounds[best][0] <= ev.bounds[a][1] + EPS]
        ev.choice = min(ev.ties, key=lambda a: arm_sort_key(a, 0, catalog, req))
    ev.dominated = [a for a in pool if any(
        cell_of[b].score >= cell_of[a].score and cell_of[b].cost <= cell_of[a].cost
        and (cell_of[b].score > cell_of[a].score or cell_of[b].cost < cell_of[a].cost)
        for b in pool if b != a)]
    return ev


def wins(arm: Arm, ev: GroupEval) -> bool:
    """The arm is the group's deterministic lowest-E choice."""
    return arm == ev.choice


def group_ref(ev: GroupEval) -> str:
    flag = "; vendor chart includes rival vendor" if ev.group.cross_vendor_vendor_chart else ""
    return f"{ev.group.gid} [{'/'.join(sorted(ev.group.source_types))}, w={ev.weight:g}{flag}]"


def decide(op: str, req: dict, catalog: Catalog, makers: dict[str, Arm], current: dict) -> dict:
    """Steps 1-5 for one op."""
    arms, excluded, notes = candidate_arms(req, catalog, makers)
    result: dict[str, Any] = {
        "op": op,
        "current": current,
        "price_horizon": {"as_of": req.get("price_as_of", dt.date.today().isoformat()),
                          "days": req.get("policy_horizon_days", 90)},
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
    incumbent = current_arm({"expands_to": current}, catalog)
    if incumbent:
        lane = incumbent.lane
        known = catalog.models.get(incumbent.model)
        dispatchable = bool(known and lane in LANES and
                            (incumbent.dispatch_model or incumbent.model) in
                            {dispatch for dispatch, _ in dispatches_for_lane(lane, known, catalog)})
        if lane == "grok" and incumbent.model not in GROK_LIVE_MODELS:
            dispatchable = False
        if not dispatchable:
            result["flags"].append(f"current expands_to {incumbent.label()} is not dispatchable on {lane}'s live CLI model list")
    for c in req.get("constraints", []):
        maker_op = parse_constraint(c)[1]
        if maker_op in makers:
            result["flags"].append(f"{c}: maker arm {makers[maker_op].label()}")
    maker = next((makers[d] for d in op_dependencies(req) if d in makers), None)
    if not arms:
        result["status"] = "NO_CANDIDATES"
        return result

    groups, skipped = build_groups(req, catalog)
    result["skipped_groups"] = skipped
    evals = [evaluate_group(g, arms, req, catalog) for g in groups]
    evals = [ev for ev in evals if ev.present]

    # Screen missing allowed efforts only when a measured arm dominates on both
    # score and task cost. Single-arm and non-success metrics cannot establish this.
    candidate_models = {a.model for a in arms}
    ceiling_excluded: dict[str, list[str]] = {}
    for ev in evals:
        if ev.ceiling_value is None:
            continue
        present_models = {a.model for a in ev.present}
        best: dict[str, tuple[float, str, float | None]] = {}
        for (mid, eff), cell in ev.group.cells.items():
            if mid in candidate_models and mid not in present_models:
                if mid not in best or cell.score > best[mid][0]:
                    best[mid] = (cell.score, eff, cell.cost)
        for mid, (score, eff, missing_cost) in sorted(best.items()):
            missing_cell = ev.group.cells[(mid, eff)]
            if missing_cost is None or not any(
                score_interval(ev.group, ev.group.cells[(a.model, a.effort)])[0]
                > score_interval(ev.group, missing_cell)[1] + EPS
                and ev.group.cells[(a.model, a.effort)].cost is not None
                and ev.group.cells[(a.model, a.effort)].cost <= missing_cost + EPS
                for a in ev.present
            ):
                continue
            raw = score if ev.group.direction == "higher_better" else -score
            ceiling_raw = ev.ceiling_value if ev.group.direction == "higher_better" else -ev.ceiling_value
            why = f"{ev.group.gid}: {mid}@{eff}={raw:g} costs ${missing_cost:g} and is dominated below {ceiling_raw:g}"
            if ev.group.kind() == "independent":
                ceiling_excluded.setdefault(mid, []).append(why)
            else:
                result["flags"].append(f"ceiling (vendor group, not excluding): {why}")
    if ceiling_excluded:
        for mid, whys in ceiling_excluded.items():
            result["excluded_models"].append({"model": mid, "reason": "dominated at its best measured effort: " + "; ".join(whys)})
        arms = [a for a in arms if a.model not in ceiling_excluded]
        result["candidates"] = [a.label() for a in arms]
        result["flags"].append("domination screen applied to missing allowed efforts")
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
            "ties": [a.label() for a in ev.ties] if len(ev.ties) > 1 else [],
            "arms": [
                {"arm": a.label(), "score": cells[(a.model, a.effort)].raw, "cost": cells[(a.model, a.effort)].cost,
                 "p": success_probability(ev.group, cells[(a.model, a.effort)]),
                 "ci_lo": cells[(a.model, a.effort)].ci_lo,
                 "ci_hi": cells[(a.model, a.effort)].ci_hi,
                 "n": cells[(a.model, a.effort)].n,
                 "price_note": cells[(a.model, a.effort)].price_note,
                 "expected_cost_usd": report_cost(ev.costs.get(a)),
                 "expected_cost_interval_usd": [report_cost(v) for v in ev.bounds[a]] if a in ev.bounds else None,
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
        if len(ev.ties) > 1:
            result["flags"].append(f"{ev.group.gid}: E within sampling noise; tied arms "
                                   + ", ".join(a.label() for a in ev.ties))
        for a in ev.present:
            note = ev.group.cells[(a.model, a.effort)].price_note
            if note:
                result["flags"].append(f"{ev.group.gid} {a.label()}: {note}")

    stats: dict[Arm, dict] = {}
    current_candidate = current_arm({"expands_to": current}, catalog)
    independent_cross = [ev for ev in decisive if ev.group.kind() == "independent"
                         and ev.group.models_present(ev.present) >= 2]
    rival_choices = {ev.choice for ev in decisive if ev.choice is not None}
    for a in rival_choices:
        containing = [ev for ev in decisive if a in ev.present]
        # An independent rival win counts against an arm even if that board omits it.
        counted = containing + [ev for ev in independent_cross if all(ev is not old for old in containing)]
        won = [ev for ev in counted if wins(a, ev)]
        num = sum(ev.weight for ev in won)
        den = sum(ev.weight for ev in counted)
        cross_num = sum(ev.weight for ev in independent_cross if ev in won)
        cross_den = sum(ev.weight for ev in independent_cross)
        rivals = {b for b in rival_choices if b.model != a.model}
        rivals.update(b for ev in won for b in ev.present if b.model != a.model)
        if current_candidate and current_candidate.model != a.model:
            rivals.add(current_candidate)
        missing_pairs = []
        for b in rivals:
            if not any((a.model, a.effort) in ev.group.cells and
                       (b.model, b.effort) in ev.group.cells for ev in independent_cross):
                boards = [ev for ev in independent_cross if (b.model, b.effort) in ev.group.cells and ev.choice == b]
                boards = boards or [ev for ev in independent_cross if a in ev.present]
                if not boards:
                    boards = independent_cross[:1]
                for ev in boards:
                    absent = a if (a.model, a.effort) not in ev.group.cells else b
                    missing_pairs.append({"model": absent.model, "effort": absent.effort,
                                          "metric": ev.group.metric_id, "group": ev.group.gid,
                                          "need": f"independent cross-model comparison with {b.model if absent == a else a.model}"})
        threshold = req.get("majority_threshold", 0.5)
        stats[a] = {"won": won, "lost": [ev for ev in counted if ev not in won],
                    "num": num, "den": den, "cross_num": cross_num, "cross_den": cross_den,
                    "missing_pairs": missing_pairs,
                    "consistent": (den > 0 and num / den > threshold
                                   and cross_den > 0 and cross_num / cross_den > threshold
                                   and not missing_pairs)}

    consistent = sorted((a for a, s in stats.items() if s["consistent"]),
                        key=lambda a: (-stats[a]["num"], horizon_price(catalog, req, a.model),
                                       tuple(-x for x in version_key(a.model))))

    def unmeasured() -> None:
        """Report absent per-task evidence without a list-token-price shortcut."""
        measured = {a.model for ev in usable for a in ev.present}
        success_metrics = [mid for mid in req["evidence_metrics"]
                           if catalog.metrics[mid].get("unit_default") in SUCCESS_UNITS]
        if not success_metrics:
            success_metrics = [ev.group.metric_id for ev in usable]
        for model in sorted({a.model for a in arms} - measured):
            boards = [ev for ev in independent_cross if model not in {a.model for a in ev.present}]
            for ev in boards:
                result["missing"].append({"model": model, "effort": None,
                                          "metric": ev.group.metric_id, "group": ev.group.gid,
                                          "need": "success-rate score and task cost at an allowed effort"})
            if not boards:
                result["missing"].append({"model": model, "effort": None,
                                          "metric": "any of " + ", ".join(success_metrics or req["evidence_metrics"]),
                                          "group": None, "need": "independent success-rate score and task cost at an allowed effort"})

    if len(consistent) == 1:
        a = consistent[0]
        s = stats[a]
        result["status"] = "RECOMMENDED"
        result["recommended"] = arm_dict(a)
        result["deciding_groups"] = [group_ref(ev) for ev in s["won"]]
        result["disagreements"] = [f"{group_ref(ev)}: choice {ev.choice.label()}" for ev in s["lost"]]
        independent = [ev for ev in s["won"] if ev.group.kind() == "independent"]
        cross_model = [ev for ev in independent if ev.group.models_present(ev.present) >= 2]
        if len(cross_model) >= req.get("high_confidence_groups", 2) and not s["lost"]:
            result["confidence"] = "high"
        elif cross_model:
            result["confidence"] = "medium"
        else:
            result["confidence"] = "low"
        if all(ev.group.cross_vendor_vendor_chart for ev in s["won"]):
            result["flags"].append("every deciding group is a vendor chart ranking a rival vendor")
        unmeasured()
    else:
        result["status"] = "INSUFFICIENT_EVIDENCE"
        for s in stats.values():
            result["missing"].extend(s["missing_pairs"])
        if len(consistent) > 1:
            # Rivals that never meet in a decisive group: the missing cross-coverage decides it.
            for a in consistent:
                result["disagreements"].append(
                    f"{a.label()} wins {stats[a]['num']:g}/{stats[a]['den']:g} weight in "
                    + ", ".join(ev.group.gid for ev in stats[a]["won"])
                    + ("; loses " + ", ".join(f"{ev.group.gid} to {ev.choice.label()}" for ev in stats[a]["lost"])
                       if stats[a]["lost"] else ""))
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
                if not s["cross_num"]:
                    why.append("no independent cross-model win")
                if s["missing_pairs"]:
                    why.append(f"{len(s['missing_pairs'])} missing independent model/board pair(s)")
                why.extend(f"{ev.group.gid}: choice {ev.choice.label()}" for ev in s["lost"])
                result["disagreements"].append(f"{a.label()}: " + "; ".join(why))
            if not decisive:
                result["flags"].append("no group has a priced success-rate arm")
        unmeasured()
    # de-duplicate missing entries, keep order
    seen = set()
    uniq = []
    for m in result["missing"]:
        key = json.dumps(m, sort_keys=True)
        if key not in seen:
            seen.add(key)
            uniq.append(m)
    result["missing"] = uniq
    if result["status"] == "INSUFFICIENT_EVIDENCE" and len({a.model for a in arms}) == 1 and len({a.lane for a in arms}) == 1:
        model = arms[0].model
        preferred = next((a for a in arms if a.effort == "medium"), arms[0])
        result["recommended"] = arm_dict(preferred)
        result["status"] = "RECOMMENDED"
        result["confidence"] = "rule-1 model only"
        result["flags"].append(f"rule 1 selects sole newest-in-tier model {model}; effort defaults to {preferred.effort} without comparative evidence")
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
              scenario: tuple[float, float, float] | dict | None = None) -> list[dict]:
    makers: dict[str, Arm] = {}
    out: dict[str, dict] = {}
    for op in reqs["order"]:
        if op not in reqs["ops"]:
            continue
        req = dict(reqs["ops"][op])
        if isinstance(scenario, tuple):
            overhead, detection, silent_multiplier = scenario
            req["attempt_overhead_usd"] = overhead
            if req["failure_detection_probability"] > 0:
                req["failure_detection_probability"] = detection
            req["silent_failure_cost_usd"] *= silent_multiplier
        elif isinstance(scenario, dict):
            req["attempt_overhead_usd"] = scenario.get("overhead", req["attempt_overhead_usd"])
            if req["failure_detection_probability"] > 0:
                req["failure_detection_probability"] = scenario.get("d", req["failure_detection_probability"])
            req["silent_failure_cost_usd"] *= scenario.get("silent_multiplier", 1)
            req["vendor_cross_vendor_factor"] = scenario.get("vendor_factor", req["vendor_cross_vendor_factor"])
            req["majority_threshold"] = scenario.get("majority", req.get("majority_threshold", 0.5))
            req["high_confidence_groups"] = scenario.get("high_confidence_groups", req.get("high_confidence_groups", 2))
            if "source_weights" in scenario:
                req["source_weights"] = {**req["source_weights"], **scenario["source_weights"]}
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
ROBUSTNESS_DETECTION = (0.25, 0.5, 0.75, 0.95)
ROBUSTNESS_SILENT_MULTIPLIER = (0.5, 1.0, 2.0)
ROBUSTNESS_SOURCE_WEIGHT = (0.5, 1.0, 2.0)
ROBUSTNESS_VENDOR_FACTOR = (0.25, 0.5, 1.0)
ROBUSTNESS_MAJORITY = (0.4, 0.5, 0.6)
ROBUSTNESS_HIGH_CONFIDENCE = (1, 2, 3)


def outcome(result: dict) -> str:
    rec = result["recommended"]
    return f"{rec['provider']}/{rec['model']}/{rec['effort']} ({result['confidence']})" if rec else result["status"]


def assess_robustness(catalog: Catalog, reqs: dict, points: dict[str, dict], results: list[dict]) -> None:
    """Keep the original cost/detection grid and sweep the added policy knobs."""
    scenarios: list[tuple[str, dict]] = [("base", {})]
    scenarios.extend((f"overhead={h:g},d={d:g},silent_multiplier={s:g}",
                      {"overhead": h, "d": d, "silent_multiplier": s})
                     for h, d, s in itertools.product(ROBUSTNESS_OVERHEAD, ROBUSTNESS_DETECTION,
                                                       ROBUSTNESS_SILENT_MULTIPLIER))
    for name, values in (("vendor_factor", ROBUSTNESS_VENDOR_FACTOR),
                         ("majority", ROBUSTNESS_MAJORITY),
                         ("high_confidence_groups", ROBUSTNESS_HIGH_CONFIDENCE)):
        scenarios.extend((f"{name}={value:g}", {name: value}) for value in values)
    for value in ROBUSTNESS_SOURCE_WEIGHT:
        scenarios.append((f"independent_weight={value:g}",
                          {"source_weights": {kind: value for kind in INDEPENDENT_SOURCE_TYPES}}))
        scenarios.append((f"vendor_weight={value:g}",
                          {"source_weights": {kind: value for kind in VENDOR_SOURCE_TYPES}}))
    cache = [(name, {r["op"]: outcome(r) for r in recommend(catalog, reqs, points, scenario)})
             for name, scenario in scenarios]
    for result in results:
        op = result["op"]
        outcomes = sorted({values[op] for _, values in cache})
        base = cache[0][1][op]
        flip = next((f"{name}: {base} → {values[op]}" for name, values in cache[1:]
                     if values[op] != base), None)
        result["robustness"] = {
            "status": "CLEAR" if len(outcomes) == 1 and result["recommended"] else "ASSUMPTION_SENSITIVE",
            "outcomes": outcomes,
            "threshold": flip or ("No winning arm in the sensitivity sweeps; evidence remains insufficient."
                                  if not result["recommended"] else None),
        }


# --------------------------------------------------------------- output


def _cell(items: list[str], limit: int = 3) -> str:
    if not items:
        return "—"
    shown = items[:limit]
    more = f" (+{len(items) - limit} more)" if len(items) > limit else ""
    return "<br>".join(s.replace("|", "\\|") for s in shown) + more


def render_markdown(results: list[dict]) -> str:
    horizon = results[0]["price_horizon"] if results else {"as_of": dt.date.today().isoformat(), "days": 90}
    lines = [f"Price policy: {horizon['days']}-day horizon from {horizon['as_of']}. E is expected cost per task.", "",
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
            notable = [e for e in r["excluded_models"] if not e["reason"].startswith("superseded in tier")]
            shown = "; ".join(f"{e['model']} ({e['reason']})" for e in notable[:5])
            extra = f"; {len(notable)-5} more in JSON" if len(notable) > 5 else ""
            lines.append(f"- excluded: {len(r['excluded_models'])} entries" + (f"; {shown}{extra}" if shown else " (superseded tiers)"))
        for f in r["flags"]:
            lines.append(f"- flag: {f}")
        for g in r["groups"]:
            arms = ", ".join(
                f"{a['arm']}={a['score']:g}" + (f" p={a['p']:.3g}" if a["p"] is not None else " p=—")
                + (f" CI=[{a['ci_lo']:.3g},{a['ci_hi']:.3g}]" if a["ci_lo"] is not None and a["ci_hi"] is not None else "")
                + (f" n={a['n']}" if a["n"] is not None else "")
                + (f" cost=${a['cost']:g}" if a["cost"] is not None else " cost=?")
                + (" E=∞" if a["expected_cost_usd"] == "Infinity" else
                   f" E=${a['expected_cost_usd']:.3g}" if a["expected_cost_usd"] is not None else " E=—")
                + (" dominated" if a["dominated"] else "") for a in g["arms"])
            status = f"unusable: {g['unusable']}" if g["unusable"] else f"choice {g['choice'] or 'none (no priced success-rate arm)'}"
            ceiling = "" if g["ceiling_raw"] is None else f", ceiling {g['ceiling_raw']:.4g}"
            flag = ", vendor chart includes rival vendor" if g["vendor_chart_ranks_rival_vendor"] else ""
            maker = (f", maker p={g['maker_p']:.3g}, checker p={g['checker_p']:.3g}"
                     if g["maker_p"] is not None and g["checker_p"] is not None else "")
            lines.append(f"  - `{g['group']}` ({g['metric_id']}, {'/'.join(g['source_types'])}, w={g['weight']:g}{flag}{ceiling}{maker}): {status} — {arms}")
            if g["ties"]:
                lines.append("    - E tie within sampling noise: " + ", ".join(g["ties"]))
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
