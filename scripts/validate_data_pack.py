#!/usr/bin/env python3
"""Validate minnows data packs against v1 contracts.

Stdlib only. Optional: if `jsonschema` is installed, also run Draft 2020-12
validation against the shipped .schema.json files.

Usage:
  ./scripts/validate_data_pack.py                 # all packs + index
  ./scripts/validate_data_pack.py model-catalog   # one pack
  ./scripts/validate_data_pack.py --index-only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TAG_RE = re.compile(r"^data-[a-z][a-z0-9-]*-v\d+\.\d+\.\d+$")
PACK_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

PRICING_KINDS = frozenset({"api_usd", "codex_credits"})
PRICING_AGENTS = frozenset({"claude-code", "codex", "grok", "google", "other"})
RATE_FIELDS = (
    "fresh_input_per_m",
    "cache_read_per_m",
    "cache_write_per_m",
    "output_per_m",
)
PERF_PROVIDERS = frozenset({"anthropic", "openai", "xai", "google", "other"})
PERF_AXES = frozenset({"quality", "cost", "effort", "speed", "latency", "tokens"})
PERF_UNITS = frozenset({"accuracy", "pass_rate", "elo", "other"})


class Errors:
    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, path: str, msg: str) -> None:
        self.items.append(f"{path}: {msg}")

    def __bool__(self) -> bool:
        return bool(self.items)


def load_json(path: Path, errors: Errors) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.add(str(path), "file not found")
    except json.JSONDecodeError as e:
        errors.add(str(path), f"invalid JSON: {e}")
    return None


def require_keys(obj: dict, keys: tuple[str, ...], path: str, errors: Errors) -> None:
    for k in keys:
        if k not in obj:
            errors.add(path, f"missing required field '{k}'")


def validate_index(errors: Errors) -> None:
    path = DATA / "index.json"
    data = load_json(path, errors)
    if not isinstance(data, dict):
        return
    p = str(path.relative_to(REPO))
    require_keys(data, ("schema_version", "updated_at", "repo", "packs"), p, errors)
    if data.get("schema_version") != 1:
        errors.add(p, f"schema_version must be 1 (got {data.get('schema_version')!r})")
    if not DATE_RE.match(str(data.get("updated_at", ""))):
        errors.add(p, "updated_at must be YYYY-MM-DD")
    if not REPO_RE.match(str(data.get("repo", ""))) or str(data.get("repo", "")).endswith(".git"):
        errors.add(p, "repo must be owner/name without .git")
    packs = data.get("packs")
    if not isinstance(packs, dict) or not packs:
        errors.add(p, "packs must be a non-empty object")
        return
    url_keys = ("readme", "releases", "pack_json", "tarball", "tree")
    for name, entry in packs.items():
        ep = f"{p}#packs.{name}"
        if not PACK_NAME_RE.match(name):
            errors.add(ep, "invalid pack name")
        if not isinstance(entry, dict):
            errors.add(ep, "must be an object")
            continue
        require_keys(
            entry,
            ("latest_tag", "description", "readme", "releases", "pack_json", "tarball", "tree"),
            ep,
            errors,
        )
        tag = str(entry.get("latest_tag", ""))
        if not TAG_RE.match(tag):
            errors.add(ep, f"invalid latest_tag {tag!r}")
        elif not tag.startswith(f"data-{name}-v"):
            errors.add(ep, f"latest_tag {tag!r} does not match pack name {name!r}")
        for uk in url_keys:
            u = str(entry.get(uk, ""))
            if not u.startswith("https://"):
                errors.add(ep, f"{uk} must be https URL")
            if ".git/" in u or u.endswith(".git"):
                errors.add(ep, f"{uk} must not contain .git in path")
        # Cross-check pack exists
        if not (DATA / name / "pack.json").is_file():
            errors.add(ep, f"no data/{name}/pack.json on disk")


def validate_pack_envelope(pack_dir: Path, errors: Errors) -> dict | None:
    path = pack_dir / "pack.json"
    data = load_json(path, errors)
    if not isinstance(data, dict):
        return None
    p = str(path.relative_to(REPO))
    require_keys(
        data,
        ("name", "schema_version", "tag", "generated_at", "description", "files"),
        p,
        errors,
    )
    name = data.get("name")
    if name != pack_dir.name:
        errors.add(p, f"name {name!r} must equal directory {pack_dir.name!r}")
    if data.get("schema_version") != 1:
        errors.add(p, "schema_version must be 1")
    tag = str(data.get("tag", ""))
    if not TAG_RE.match(tag):
        errors.add(p, f"invalid tag {tag!r}")
    elif not tag.startswith(f"data-{pack_dir.name}-v"):
        errors.add(p, f"tag {tag!r} must start with data-{pack_dir.name}-v")
    if not DATE_RE.match(str(data.get("generated_at", ""))):
        errors.add(p, "generated_at must be YYYY-MM-DD")
    files = data.get("files")
    if not isinstance(files, list) or not files:
        errors.add(p, "files must be a non-empty array")
        return data
    seen: set[str] = set()
    for rel in files:
        if not isinstance(rel, str) or not rel or rel.startswith("/") or ".." in rel:
            errors.add(p, f"invalid files entry {rel!r}")
            continue
        if rel in seen:
            errors.add(p, f"duplicate files entry {rel!r}")
        seen.add(rel)
        fp = pack_dir / rel
        if not fp.is_file():
            errors.add(p, f"listed file missing: {rel}")
    return data


SOURCE_KINDS = frozenset(
    {"vendor_blog", "vendor_docs", "third_party_eval", "academic", "digitized_chart", "local_eval", "other"}
)
SOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9-]+$")


def load_source_registry(pack_dir: Path, errors: Errors) -> set[str]:
    """Load SOURCES.json id set. Empty set if absent (with error)."""
    path = pack_dir / "SOURCES.json"
    p = str(path.relative_to(REPO))
    if not path.is_file():
        errors.add(p, "SOURCES.json missing — required provenance registry")
        return set()
    data = load_json(path, errors)
    if not isinstance(data, dict):
        return set()
    require_keys(data, ("id", "schema_version", "retrieved_at", "sources"), p, errors)
    if data.get("schema_version") != 1:
        errors.add(p, "schema_version must be 1")
    if data.get("id") != "model-catalog-sources":
        errors.add(p, "id must be 'model-catalog-sources'")
    if not DATE_RE.match(str(data.get("retrieved_at", ""))):
        errors.add(p, "retrieved_at must be YYYY-MM-DD")
    sources = data.get("sources")
    ids: set[str] = set()
    if not isinstance(sources, list) or not sources:
        errors.add(p, "sources must be a non-empty array")
        return ids
    for i, s in enumerate(sources):
        sp = f"{p}#sources[{i}]"
        if not isinstance(s, dict):
            errors.add(sp, "must be an object")
            continue
        for k in ("id", "url", "title", "publisher", "retrieved_at", "kind"):
            if k not in s:
                errors.add(sp, f"missing {k}")
        sid = str(s.get("id", ""))
        if sid:
            if not SOURCE_ID_RE.match(sid):
                errors.add(sp, f"invalid source id {sid!r}")
            elif sid in ids:
                errors.add(sp, f"duplicate source id {sid!r}")
            else:
                ids.add(sid)
        if s.get("kind") not in SOURCE_KINDS:
            errors.add(sp, f"kind must be one of {sorted(SOURCE_KINDS)}")
        if not DATE_RE.match(str(s.get("retrieved_at", ""))):
            errors.add(sp, "retrieved_at must be YYYY-MM-DD")
        url = str(s.get("url", ""))
        if url and not url.startswith("https://"):
            errors.add(sp, "url must be https")
    return ids


def check_source_ids(
    data: dict,
    path: str,
    registry: set[str],
    errors: Errors,
    *,
    require_doc: bool = True,
) -> None:
    sids = data.get("source_ids")
    if sids is None:
        if require_doc and registry:
            errors.add(path, "source_ids[] required (link to SOURCES.json)")
        return
    if not isinstance(sids, list) or not sids:
        errors.add(path, "source_ids must be a non-empty array when present")
        return
    for sid in sids:
        if not isinstance(sid, str) or not SOURCE_ID_RE.match(sid):
            errors.add(path, f"invalid source_ids entry {sid!r}")
        elif registry and sid not in registry:
            errors.add(path, f"source_ids entry {sid!r} not in SOURCES.json")


def check_row_source_id(row: dict, row_path: str, registry: set[str], errors: Errors) -> None:
    sid = row.get("source_id")
    if sid is None:
        return
    if not isinstance(sid, str) or not SOURCE_ID_RE.match(sid):
        errors.add(row_path, f"invalid source_id {sid!r}")
    elif registry and sid not in registry:
        errors.add(row_path, f"source_id {sid!r} not in SOURCES.json")


def validate_pricing(
    path: Path,
    errors: Errors,
    registry: set[str] | None = None,
    model_registry: dict[str, str] | None = None,
) -> None:
    data = load_json(path, errors)
    if not isinstance(data, dict):
        return
    p = str(path.relative_to(REPO))
    require_keys(
        data,
        ("id", "schema_version", "kind", "agent", "retrieved_at", "source_urls", "models", "match"),
        p,
        errors,
    )
    if data.get("schema_version") != 1:
        errors.add(p, "schema_version must be 1")
    if data.get("kind") not in PRICING_KINDS:
        errors.add(p, f"kind must be one of {sorted(PRICING_KINDS)}")
    if data.get("agent") not in PRICING_AGENTS:
        errors.add(p, f"agent must be one of {sorted(PRICING_AGENTS)}")
    if not DATE_RE.match(str(data.get("retrieved_at", ""))):
        errors.add(p, "retrieved_at must be YYYY-MM-DD")
    sources = data.get("source_urls")
    if not isinstance(sources, list) or not sources:
        errors.add(p, "source_urls must be a non-empty array")
    if registry is not None:
        check_source_ids(data, p, registry, errors)
    models = data.get("models")
    if not isinstance(models, dict) or not models:
        errors.add(p, "models must be a non-empty object")
        return
    for mid, rates in models.items():
        rp = f"{p}#models.{mid}"
        if not isinstance(rates, dict):
            errors.add(rp, "must be an object")
            continue
        if model_registry is not None:
            check_model_id(mid, rp, model_registry, errors)
        for f in RATE_FIELDS:
            if f not in rates:
                errors.add(rp, f"missing {f}")
            elif not isinstance(rates[f], (int, float)) or isinstance(rates[f], bool):
                errors.add(rp, f"{f} must be a number")
            elif rates[f] < 0:
                errors.add(rp, f"{f} must be >= 0")
        allowed = set(RATE_FIELDS) | {"valid_from", "valid_until", "confidence"}
        extra = set(rates) - allowed
        if extra:
            errors.add(rp, f"unknown fields: {sorted(extra)}")
        for vk in ("valid_from", "valid_until"):
            if vk in rates and not DATE_RE.match(str(rates[vk])):
                errors.add(rp, f"{vk} must be YYYY-MM-DD")
        if "confidence" in rates and rates["confidence"] not in ("high", "medium", "low"):
            errors.add(rp, "confidence must be high|medium|low")
        vu = rates.get("valid_until")
        if isinstance(vu, str) and DATE_RE.match(vu):
            try:
                if date.fromisoformat(vu) < date.today():
                    errors.add(rp, f"pricing expired valid_until={vu} (remove or update promo rates)")
            except ValueError:
                pass
    match = data.get("match")
    if not isinstance(match, list) or not match:
        errors.add(p, "match must be a non-empty array")
        return
    for i, entry in enumerate(match):
        mp = f"{p}#match[{i}]"
        if not isinstance(entry, dict):
            errors.add(mp, "must be an object")
            continue
        if "pattern" not in entry or "model" not in entry:
            errors.add(mp, "need pattern and model")
            continue
        if entry["model"] not in models:
            errors.add(mp, f"model {entry['model']!r} not in models")


def validate_performance(
    path: Path,
    errors: Errors,
    registry: set[str] | None = None,
    model_registry: dict[str, str] | None = None,
    metric_ids: set[str] | None = None,
) -> None:
    data = load_json(path, errors)
    if not isinstance(data, dict):
        return
    p = str(path.relative_to(REPO))
    require_keys(
        data,
        ("id", "schema_version", "kind", "provider", "retrieved_at", "source_urls"),
        p,
        errors,
    )
    if data.get("schema_version") != 1:
        errors.add(p, "schema_version must be 1")
    if data.get("kind") != "performance":
        errors.add(p, "kind must be 'performance'")
    if data.get("provider") not in PERF_PROVIDERS:
        errors.add(p, f"provider must be one of {sorted(PERF_PROVIDERS)}")
    if not DATE_RE.match(str(data.get("retrieved_at", ""))):
        errors.add(p, "retrieved_at must be YYYY-MM-DD")
    sources = data.get("source_urls")
    if not isinstance(sources, list) or not sources:
        errors.add(p, "source_urls must be a non-empty array")
    if registry is not None:
        check_source_ids(data, p, registry, errors)

    claims = data.get("claims")
    scores = data.get("scores")
    has_claims = isinstance(claims, list) and len(claims) > 0
    has_scores = isinstance(scores, list) and len(scores) > 0
    if not has_claims and not has_scores:
        errors.add(p, "need non-empty claims[] and/or scores[]")

    if claims is not None:
        if not isinstance(claims, list):
            errors.add(p, "claims must be an array")
        else:
            for i, c in enumerate(claims):
                cp = f"{p}#claims[{i}]"
                if not isinstance(c, dict):
                    errors.add(cp, "must be an object")
                    continue
                if not isinstance(c.get("models"), list) or not c["models"]:
                    errors.add(cp, "models must be non-empty array")
                elif model_registry is not None:
                    for mid in c["models"]:
                        if isinstance(mid, str):
                            check_model_id(mid, cp, model_registry, errors)
                if not isinstance(c.get("statement"), str) or not c["statement"].strip():
                    errors.add(cp, "statement required")
                axes = c.get("axes")
                if axes is not None:
                    if not isinstance(axes, list):
                        errors.add(cp, "axes must be array")
                    else:
                        for a in axes:
                            if a not in PERF_AXES:
                                errors.add(cp, f"unknown axis {a!r}")
                if registry is not None:
                    check_row_source_id(c, cp, registry, errors)

    if scores is not None:
        if not isinstance(scores, list):
            errors.add(p, "scores must be an array")
        else:
            for i, s in enumerate(scores):
                sp = f"{p}#scores[{i}]"
                if not isinstance(s, dict):
                    errors.add(sp, "must be an object")
                    continue
                for k in ("model", "metric", "score", "unit"):
                    if k not in s:
                        errors.add(sp, f"missing {k}")
                if "score" in s and not isinstance(s["score"], (int, float)):
                    errors.add(sp, "score must be a number")
                if s.get("unit") not in PERF_UNITS and "unit" in s:
                    errors.add(sp, f"unit must be one of {sorted(PERF_UNITS)}")
                if model_registry is not None and isinstance(s.get("model"), str):
                    check_model_id(s["model"], sp, model_registry, errors)
                mid = s.get("metric_id")
                if metric_ids is not None and mid is not None:
                    if not isinstance(mid, str) or mid not in metric_ids:
                        errors.add(sp, f"metric_id {mid!r} not in metrics.json")
                comps = s.get("comparisons")
                if comps is not None:
                    if not isinstance(comps, dict):
                        errors.add(sp, "comparisons must be object")
                    else:
                        for mk, mv in comps.items():
                            if not isinstance(mv, (int, float)):
                                errors.add(sp, f"comparisons.{mk} must be number")
                if registry is not None:
                    check_row_source_id(s, sp, registry, errors)

    if "missing" in data and not isinstance(data["missing"], list):
        errors.add(p, "missing must be an array")



def validate_capabilities(
    path: Path,
    errors: Errors,
    registry: set[str] | None = None,
    model_registry: dict[str, str] | None = None,
) -> None:
    data = load_json(path, errors)
    if not isinstance(data, dict):
        return
    p = str(path.relative_to(REPO))
    require_keys(
        data,
        ("id", "schema_version", "kind", "retrieved_at", "source_urls", "surfaces"),
        p,
        errors,
    )
    if data.get("schema_version") != 1:
        errors.add(p, "schema_version must be 1")
    if data.get("kind") != "capabilities":
        errors.add(p, "kind must be 'capabilities'")
    if not DATE_RE.match(str(data.get("retrieved_at", ""))):
        errors.add(p, "retrieved_at must be YYYY-MM-DD")
    if registry is not None:
        check_source_ids(data, p, registry, errors, require_doc=False)
    surfaces = data.get("surfaces")
    if not isinstance(surfaces, list) or not surfaces:
        errors.add(p, "surfaces must be a non-empty array")
        return
    for i, s in enumerate(surfaces):
        sp = f"{p}#surfaces[{i}]"
        if not isinstance(s, dict):
            errors.add(sp, "must be an object")
            continue
        for k in ("provider", "model", "surface", "valid_efforts", "unsupported_behavior"):
            if k not in s:
                errors.add(sp, f"missing {k}")
        if model_registry is not None and isinstance(s.get("model"), str):
            check_model_id(s["model"], sp, model_registry, errors)
        ve = s.get("valid_efforts")
        if not isinstance(ve, list) or not ve:
            errors.add(sp, "valid_efforts must be non-empty array")



def load_model_registry(pack_dir: Path, errors: Errors) -> dict[str, str]:
    """Return map of resolvable id/alias -> canonical id. Empty if models.json missing."""
    path = pack_dir / "models.json"
    p = str(path.relative_to(REPO))
    if not path.is_file():
        errors.add(p, "models.json missing — L0 model registry required")
        return {}
    data = load_json(path, errors)
    if not isinstance(data, dict):
        return {}
    require_keys(data, ("id", "schema_version", "generated_at", "models"), p, errors)
    if data.get("schema_version") != 1:
        errors.add(p, "schema_version must be 1")
    if data.get("id") != "model-catalog-models":
        errors.add(p, "id must be 'model-catalog-models'")
    if not DATE_RE.match(str(data.get("generated_at", ""))):
        errors.add(p, "generated_at must be YYYY-MM-DD")
    models = data.get("models")
    resolve: dict[str, str] = {}
    if not isinstance(models, list) or not models:
        errors.add(p, "models must be a non-empty array")
        return resolve
    seen_ids: set[str] = set()
    for i, m in enumerate(models):
        mp = f"{p}#models[{i}]"
        if not isinstance(m, dict):
            errors.add(mp, "must be an object")
            continue
        mid = m.get("id")
        if not isinstance(mid, str) or not mid:
            errors.add(mp, "id required")
            continue
        if mid in seen_ids:
            errors.add(mp, f"duplicate model id {mid!r}")
        seen_ids.add(mid)
        if mid in resolve and resolve[mid] != mid:
            errors.add(mp, f"id {mid!r} collides with alias of {resolve[mid]!r}")
        resolve[mid] = mid
        for k in ("provider", "family", "status"):
            if k not in m:
                errors.add(mp, f"missing {k}")
        aliases = m.get("aliases") or []
        if aliases is not None and not isinstance(aliases, list):
            errors.add(mp, "aliases must be an array")
            continue
        for a in aliases or []:
            if not isinstance(a, str) or not a:
                errors.add(mp, f"invalid alias {a!r}")
                continue
            if a in resolve and resolve[a] != mid:
                errors.add(mp, f"alias {a!r} already maps to {resolve[a]!r}")
            else:
                resolve[a] = mid
    return resolve


def resolve_model_id(mid: str, registry: dict[str, str]) -> str | None:
    """Resolve model id via exact match, alias, or harness @suffix strip."""
    if not mid or not registry:
        return None
    if mid in registry:
        return registry[mid]
    if "@" in mid:
        return resolve_model_id(mid.split("@", 1)[0], registry)
    return None


def check_model_id(
    mid: str,
    path: str,
    registry: dict[str, str],
    errors: Errors,
    *,
    field: str = "model",
) -> None:
    if not registry:
        return
    if resolve_model_id(mid, registry) is None:
        errors.add(path, f"{field} {mid!r} not in models.json (id or alias)")


def validate_model_catalog(pack_dir: Path, errors: Errors) -> None:
    validate_pack_envelope(pack_dir, errors)
    source_registry = load_source_registry(pack_dir, errors)
    model_registry = load_model_registry(pack_dir, errors)
    metric_ids: set[str] = set()
    mpath = pack_dir / "metrics.json"
    if mpath.is_file():
        mdata = load_json(mpath, Errors())  # soft — full check later
        if isinstance(mdata, dict):
            for m in mdata.get("metrics") or []:
                if isinstance(m, dict) and m.get("id"):
                    metric_ids.add(str(m["id"]))
    pricing_dir = pack_dir / "pricing"
    perf_dir = pack_dir / "performance"
    if pricing_dir.is_dir():
        paths = sorted(pricing_dir.glob("*.json"))
        if not paths:
            errors.add(str(pack_dir.relative_to(REPO)), "pricing/ has no JSON tables")
        for path in paths:
            validate_pricing(path, errors, source_registry, model_registry)
    else:
        errors.add(str(pack_dir.relative_to(REPO)), "missing pricing/")
    if perf_dir.is_dir():
        for path in sorted(perf_dir.glob("*.json")):
            validate_performance(path, errors, source_registry, model_registry, metric_ids)
    cap_dir = pack_dir / "capabilities"
    if cap_dir.is_dir():
        for path in sorted(cap_dir.glob("*.json")):
            validate_capabilities(path, errors, source_registry, model_registry)


    # Comparability: metric_ids that mix source_type or harness must set comparable=false on rows
    by_mid: dict[str, list[tuple[str, str, str | None]]] = {}
    for path in sorted((pack_dir / "performance").glob("*.json")) if (pack_dir / "performance").is_dir() else []:
        data = load_json(path, Errors())
        if not isinstance(data, dict):
            continue
        for i, s in enumerate(data.get("scores") or []):
            if not isinstance(s, dict):
                continue
            mid = s.get("metric_id")
            if not isinstance(mid, str):
                continue
            by_mid.setdefault(mid, []).append(
                (
                    str(path.relative_to(REPO)) + f"#scores[{i}]",
                    str(s.get("source_type") or "unknown"),
                    s.get("harness"),
                    s.get("comparable"),
                )
            )
    for mid, rows in by_mid.items():
        types = {r[1] for r in rows}
        harnesses = {r[2] for r in rows}
        if len(types) > 1 or len(harnesses) > 1:
            for rp, st, h, comp in rows:
                if comp is not False:
                    errors.add(
                        rp,
                        f"metric_id {mid!r} mixes source/harness classes; set comparable=false "
                        f"(got source_type={st!r} harness={h!r} comparable={comp!r})",
                    )

    for name in (
        "pricing-v1.schema.json",
        "performance-v1.schema.json",
        "sources-v1.schema.json",
    ):
        if not (pack_dir / "schemas" / name).is_file():
            errors.add(str(pack_dir.relative_to(REPO)), f"missing schemas/{name}")
    if not (pack_dir / "SCHEMA.md").is_file() and not (pack_dir / "schemas" / "README.md").is_file():
        errors.add(str(pack_dir.relative_to(REPO)), "missing SCHEMA.md or schemas/README.md")
    # metrics.json optional but if present must be well-formed
    mpath = pack_dir / "metrics.json"
    if mpath.is_file():
        mdata = load_json(mpath, errors)
        mp = str(mpath.relative_to(REPO))
        if isinstance(mdata, dict):
            require_keys(mdata, ("id", "schema_version", "generated_at", "metrics"), mp, errors)
            if mdata.get("id") != "model-catalog-metrics":
                errors.add(mp, "id must be 'model-catalog-metrics'")
            if not isinstance(mdata.get("metrics"), list) or not mdata["metrics"]:
                errors.add(mp, "metrics must be non-empty array")



def validate_policy_pack(pack_dir: Path, errors: Errors) -> None:
    """Validate model-choice-policy: envelope, ops integrity, catalog pin, evidence refs."""
    envelope = validate_pack_envelope(pack_dir, errors)
    path = pack_dir / "operating-points.json"
    p = str(path.relative_to(REPO))
    data = load_json(path, errors)
    if not isinstance(data, dict):
        return

    require_keys(
        data,
        ("id", "schema_version", "policy_version", "generated_at", "catalog_ref", "operating_points"),
        p,
        errors,
    )
    if data.get("schema_version") != 1:
        errors.add(p, "schema_version must be 1")
    if data.get("id") != "model-choice-policy":
        errors.add(p, "id must be 'model-choice-policy'")
    if not DATE_RE.match(str(data.get("generated_at", ""))):
        errors.add(p, "generated_at must be YYYY-MM-DD")

    catalog_ref = str(data.get("catalog_ref", ""))
    if not TAG_RE.match(catalog_ref) or not catalog_ref.startswith("data-model-catalog-v"):
        errors.add(p, f"catalog_ref must be data-model-catalog-vX.Y.Z (got {catalog_ref!r})")

    # pack.json related.catalog must agree
    if isinstance(envelope, dict):
        related = envelope.get("related") or {}
        if isinstance(related, dict):
            rel_cat = related.get("catalog")
            if rel_cat and rel_cat != catalog_ref:
                errors.add(
                    str((pack_dir / "pack.json").relative_to(REPO)),
                    f"related.catalog {rel_cat!r} != operating-points catalog_ref {catalog_ref!r}",
                )
        # pack tag vs policy_version
        tag = str(envelope.get("tag", ""))
        pv = str(data.get("policy_version", ""))
        if tag and pv and not tag.endswith(f"-v{pv}"):
            errors.add(
                str((pack_dir / "pack.json").relative_to(REPO)),
                f"tag {tag!r} should end with -v{pv} (policy_version)",
            )

    # Load pinned catalog if present on disk (same repo)
    catalog_dir = DATA / "model-catalog"
    model_registry: dict[str, str] = {}
    source_ids: set[str] = set()
    catalog_file_ids: set[str] = set()  # stem of pricing/performance json
    effort_surfaces: list[dict] = []
    if catalog_dir.is_dir():
        model_registry = load_model_registry(catalog_dir, Errors())  # soft: don't double-count models.json errors
        # re-load models without polluting if already validated; if empty, try direct
        if not model_registry and (catalog_dir / "models.json").is_file():
            model_registry = load_model_registry(catalog_dir, errors)
        src = load_json(catalog_dir / "SOURCES.json", Errors())
        if isinstance(src, dict):
            for s in src.get("sources") or []:
                if isinstance(s, dict) and s.get("id"):
                    source_ids.add(str(s["id"]))
        for sub in ("pricing", "performance", "capabilities"):
            d = catalog_dir / sub
            if d.is_dir():
                for f in d.glob("*.json"):
                    catalog_file_ids.add(f"{sub}/{f.stem}")
        cap_path = catalog_dir / "capabilities" / "effort-surfaces-2026-07.json"
        if cap_path.is_file():
            cap = load_json(cap_path, Errors())
            if isinstance(cap, dict) and isinstance(cap.get("surfaces"), list):
                effort_surfaces = [s for s in cap["surfaces"] if isinstance(s, dict)]
        # catalog pack tag should match pin when local
        cpack = load_json(catalog_dir / "pack.json", Errors())
        if isinstance(cpack, dict):
            local_tag = cpack.get("tag")
            if local_tag and local_tag != catalog_ref:
                errors.add(
                    p,
                    f"catalog_ref {catalog_ref!r} does not match local model-catalog tag {local_tag!r}",
                )
    else:
        errors.add(p, "cannot validate evidence_refs: data/model-catalog missing")

    ops = data.get("operating_points")
    if not isinstance(ops, list) or not ops:
        errors.add(p, "operating_points must be a non-empty array")
        return

    op_ids: set[str] = set()
    for i, op in enumerate(ops):
        op_path = f"{p}#operating_points[{i}]"
        if not isinstance(op, dict):
            errors.add(op_path, "must be an object")
            continue
        oid = op.get("id")
        if not isinstance(oid, str) or not oid:
            errors.add(op_path, "id required")
            continue
        if oid in op_ids:
            errors.add(op_path, f"duplicate op id {oid!r}")
        op_ids.add(oid)
        for k in ("task_family", "constraint_family", "expands_to"):
            if k not in op:
                errors.add(op_path, f"missing {k}")
        expands = op.get("expands_to")
        if not isinstance(expands, dict):
            errors.add(op_path, "expands_to must be an object")
            continue
        provider = expands.get("provider")
        model = expands.get("model")
        effort = expands.get("effort")
        if not provider:
            errors.add(op_path, "expands_to.provider required")
        if isinstance(model, str) and model_registry:
            check_model_id(model, f"{op_path}.expands_to", model_registry, errors)
        # capabilities surface check
        if effort_surfaces and isinstance(model, str) and isinstance(effort, str) and provider:
            matches = [
                s
                for s in effort_surfaces
                if s.get("model") == model
                or resolve_model_id(str(s.get("model", "")), model_registry or {})
                == resolve_model_id(model, model_registry or {})
            ]
            # filter by provider if surfaces declare it
            prov_matches = [s for s in matches if s.get("provider") == provider] or matches
            if not prov_matches:
                errors.add(
                    op_path,
                    f"expands_to model {model!r} provider {provider!r} has no capabilities surface",
                )
            else:
                ok_effort = False
                for s in prov_matches:
                    ve = s.get("valid_efforts") or []
                    if effort in ve:
                        ok_effort = True
                        break
                if not ok_effort:
                    errors.add(
                        op_path,
                        f"effort {effort!r} not in valid_efforts for model {model!r}",
                    )

        # evidence_refs
        for j, ref in enumerate(op.get("evidence_refs") or []):
            rp = f"{op_path}.evidence_refs[{j}]"
            if not isinstance(ref, str):
                errors.add(rp, "must be string")
                continue
            if ref.startswith("catalog://"):
                rest = ref[len("catalog://") :]
                # allow catalog://pricing/foo or catalog://performance/foo
                if rest not in catalog_file_ids and f"{rest}" not in catalog_file_ids:
                    # also try without checking exact — stem under subdir
                    parts = rest.split("/", 1)
                    if len(parts) != 2 or f"{parts[0]}/{parts[1]}" not in catalog_file_ids:
                        errors.add(rp, f"unresolved catalog ref {ref!r}")
            elif ref.startswith("source://"):
                sid = ref[len("source://") :]
                if source_ids and sid not in source_ids:
                    errors.add(rp, f"source id {sid!r} not in catalog SOURCES.json")
            else:
                errors.add(rp, f"evidence_ref must be catalog:// or source:// (got {ref!r})")

    # escalate/deescalate graph
    for i, op in enumerate(ops):
        if not isinstance(op, dict):
            continue
        op_path = f"{p}#operating_points[{i}]"
        for field in ("escalate_to", "deescalate_to"):
            targets = op.get(field) or []
            if not isinstance(targets, list):
                errors.add(op_path, f"{field} must be an array")
                continue
            for t in targets:
                if t not in op_ids:
                    errors.add(op_path, f"{field} target {t!r} is not a known op id")


def try_jsonschema(errors: Errors, pack: str | None) -> None:
    try:
        import jsonschema  # type: ignore
        from jsonschema import Draft202012Validator
    except ImportError:
        return

    def check(instance_path: Path, schema_path: Path) -> None:
        inst = load_json(instance_path, errors)
        schema = load_json(schema_path, errors)
        if not isinstance(inst, dict) or not isinstance(schema, dict):
            return
        validator = Draft202012Validator(schema)
        for err in sorted(validator.iter_errors(inst), key=lambda e: list(e.path)):
            loc = "/".join(str(x) for x in err.path) or "(root)"
            errors.add(f"{instance_path.relative_to(REPO)}[{loc}]", err.message)

    check(DATA / "index.json", DATA / "schemas" / "index-v1.schema.json")
    packs = [pack] if pack else [d.name for d in DATA.iterdir() if (d / "pack.json").is_file()]
    for name in packs:
        pdir = DATA / name
        check(pdir / "pack.json", DATA / "schemas" / "pack-v1.schema.json")
        if name == "model-catalog":
            src = pdir / "SOURCES.json"
            if src.is_file():
                check(src, pdir / "schemas" / "sources-v1.schema.json")
            for path in sorted((pdir / "pricing").glob("*.json")):
                check(path, pdir / "schemas" / "pricing-v1.schema.json")
            for path in sorted((pdir / "performance").glob("*.json")):
                check(path, pdir / "schemas" / "performance-v1.schema.json")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pack", nargs="?", help="Pack name (default: all)")
    ap.add_argument("--index-only", action="store_true")
    ap.add_argument("--no-jsonschema", action="store_true", help="Skip optional jsonschema module")
    ap.add_argument("--require-jsonschema", action="store_true", help="Fail if jsonschema is not installed")
    args = ap.parse_args()

    errors = Errors()
    if not args.pack or args.index_only:
        validate_index(errors)
    if not args.index_only:
        if args.pack:
            pdir = DATA / args.pack
            if not pdir.is_dir():
                errors.add(args.pack, "pack directory not found")
            elif args.pack == "model-catalog":
                validate_model_catalog(pdir, errors)
            elif args.pack == "model-choice-policy":
                validate_policy_pack(pdir, errors)
            else:
                validate_pack_envelope(pdir, errors)
        else:
            for pdir in sorted(DATA.iterdir()):
                if (pdir / "pack.json").is_file():
                    if pdir.name == "model-catalog":
                        validate_model_catalog(pdir, errors)
                    elif pdir.name == "model-choice-policy":
                        validate_policy_pack(pdir, errors)
                    else:
                        validate_pack_envelope(pdir, errors)

    if args.require_jsonschema:
        try:
            import jsonschema  # noqa: F401
        except ImportError:
            errors.add("jsonschema", "required but not installed (pip install jsonschema)")
    if not args.no_jsonschema and not args.index_only:
        try_jsonschema(errors, args.pack)

    if errors:
        print("validate-data-pack: FAIL", file=sys.stderr)
        for line in errors.items:
            print(f"  • {line}", file=sys.stderr)
        return 1
    print("validate-data-pack: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
