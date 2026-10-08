#!/usr/bin/env python3
"""Validate minnows data packs against v1 contracts.

Stdlib only. Optional: if `jsonschema` is installed, also run Draft 2020-12
validation against the shipped .schema.json files.

Usage:
  ./scripts/validate_data_pack.py                 # all packs + index
  ./scripts/validate_data_pack.py model-catalog   # one pack
  ./scripts/validate_data_pack.py decision-model-catalog   # shares code with model-catalog (PackProfile)
  ./scripts/validate_data_pack.py --index-only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
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
PRICING_AGENTS = frozenset({"claude-code", "codex", "grok", "google", "qwen-code", "other"})
RATE_FIELDS = (
    "fresh_input_per_m",
    "cache_read_per_m",
    "cache_write_per_m",
    "output_per_m",
)
PERF_PROVIDERS = frozenset({"anthropic", "openai", "xai", "google", "alibaba", "deepseek", "xiaomi", "other"})
PERF_AXES = frozenset({"quality", "cost", "effort", "speed", "latency", "tokens"})
PERF_UNITS = frozenset({"accuracy", "pass_rate", "error_rate", "elo", "other"})
SOURCE_KINDS = frozenset(
    {"vendor_blog", "vendor_docs", "third_party_eval", "academic", "digitized_chart", "local_eval", "other"}
)
SOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9-]+$")

# --- decision-model-catalog vocabularies -------------------------------------
DECISION_PERF_UNITS = PERF_UNITS | {"ms", "ece", "brier", "usd_per_decision", "ratio"}
DECISION_SOURCE_KINDS = SOURCE_KINDS | {
    "model_card", "vendor_changelog", "third_party_blog", "community_post",
}
DECISION_MODEL_STATUSES = frozenset({
    "ga", "preview", "early_access", "limited_preview", "community_finetune",
    "research_release", "historical", "deprecated", "third_party_board_only",
})
DECISION_SURFACES = frozenset({
    "typesafe-api", "workers-ai", "openai-api", "openrouter", "self-host", "hf-inference", "other",
})
DECISION_WEIGHTS = frozenset({"open", "closed"})
CACHE_STATUSES = frozenset({"published", "not_published", "vendor_states_none_charged"})
DECISION_BILLING_BASES = frozenset({"per_token", "per_request", "per_decision", "raw_units", "free", "self_host"})
LATENCY_VANTAGES = frozenset({
    "hosted_rtt", "on_platform", "on_card", "local_cpu", "local_gpu", "vendor_claim",
})
LATENCY_PERCENTILES = frozenset({"p50", "p95", "p99", "mean"})
CAVEAT_STRENGTHS = frozenset({"strong", "moderate", "anecdotal"})
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]*$")


@dataclass(frozen=True)
class PackProfile:
    """Per-pack knobs for the shared catalog validation functions.

    MODEL_CATALOG reproduces the historical behavior exactly; DECISION_CATALOG
    layers the decision-model extensions on the same code paths.
    """

    name: str
    allow_empty: bool = False  # empty sources/models/metrics/pricing dir are valid seeds
    strict_files: bool = False  # every non-dot file on disk must be listed in pack.json
    decision: bool = False  # enable decision-model row rules, caveats, measurement
    perf_units: frozenset = PERF_UNITS
    perf_providers: frozenset | None = PERF_PROVIDERS  # None = any slug
    source_kinds: frozenset = SOURCE_KINDS
    baseline: str | None = None  # pack dir name whose models.json is a fallback resolver
    required_schemas: tuple = ("pricing-v1.schema.json", "performance-v1.schema.json", "sources-v1.schema.json")


MODEL_CATALOG = PackProfile(name="model-catalog")
DECISION_CATALOG = PackProfile(
    name="decision-model-catalog",
    allow_empty=True,
    strict_files=True,
    decision=True,
    perf_units=DECISION_PERF_UNITS,
    perf_providers=None,
    source_kinds=DECISION_SOURCE_KINDS,
    baseline="model-catalog",
    required_schemas=(
        "pricing-v1.schema.json", "performance-v1.schema.json", "sources-v1.schema.json",
        "models-v1.schema.json", "metrics-v1.schema.json", "capabilities-v1.schema.json",
        "caveats-v1.schema.json", "pack-v1.schema.json",
    ),
)
PROFILES = {p.name: p for p in (MODEL_CATALOG, DECISION_CATALOG)}


def rel(path: Path) -> str:
    """Repo-relative path for messages; falls back to the absolute path (tests use tmp dirs)."""
    try:
        return str(Path(path).relative_to(REPO))
    except ValueError:
        return str(path)


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
    p = rel(path)
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


def validate_pack_envelope(
    pack_dir: Path, errors: Errors, *, strict_files: bool = False
) -> dict | None:
    path = pack_dir / "pack.json"
    data = load_json(path, errors)
    if not isinstance(data, dict):
        return None
    p = rel(path)
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
    for entry in files:
        if not isinstance(entry, str) or not entry or entry.startswith("/") or ".." in entry:
            errors.add(p, f"invalid files entry {entry!r}")
            continue
        if entry in seen:
            errors.add(p, f"duplicate files entry {entry!r}")
        seen.add(entry)
        fp = pack_dir / entry
        if not fp.is_file():
            errors.add(p, f"listed file missing: {entry}")
    if strict_files:
        on_disk = {
            f.relative_to(pack_dir).as_posix()
            for f in pack_dir.rglob("*")
            if f.is_file() and not any(part.startswith(".") for part in f.relative_to(pack_dir).parts)
        } - {"pack.json"}
        for extra in sorted(on_disk - seen):
            errors.add(p, f"file on disk not listed in pack.json: {extra}")
    return data


def load_source_registry(
    pack_dir: Path, errors: Errors, profile: PackProfile = MODEL_CATALOG
) -> set[str]:
    """Load SOURCES.json id set. Empty set if absent (with error)."""
    path = pack_dir / "SOURCES.json"
    p = rel(path)
    if not path.is_file():
        errors.add(p, "SOURCES.json missing — required provenance registry")
        return set()
    data = load_json(path, errors)
    if not isinstance(data, dict):
        return set()
    require_keys(data, ("id", "schema_version", "retrieved_at", "sources"), p, errors)
    if data.get("schema_version") != 1:
        errors.add(p, "schema_version must be 1")
    if data.get("id") != f"{profile.name}-sources":
        errors.add(p, f"id must be '{profile.name}-sources'")
    if not DATE_RE.match(str(data.get("retrieved_at", ""))):
        errors.add(p, "retrieved_at must be YYYY-MM-DD")
    sources = data.get("sources")
    ids: set[str] = set()
    if not isinstance(sources, list) or (not sources and not profile.allow_empty):
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
        if s.get("kind") not in profile.source_kinds:
            errors.add(sp, f"kind must be one of {sorted(profile.source_kinds)}")
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


def check_caveat_ids(
    row: dict, row_path: str, caveat_ids: set[str] | None, errors: Errors
) -> None:
    """Rows may cite caveats by id; every cited id must exist in caveats.json."""
    cids = row.get("caveat_ids")
    if cids is None or caveat_ids is None:
        return
    if not isinstance(cids, list) or not cids:
        errors.add(row_path, "caveat_ids must be a non-empty array when present")
        return
    for cid in cids:
        if not isinstance(cid, str) or not SOURCE_ID_RE.match(cid):
            errors.add(row_path, f"invalid caveat_ids entry {cid!r}")
        elif cid not in caveat_ids:
            errors.add(row_path, f"caveat_ids entry {cid!r} not in caveats.json")


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check_decision_price_row(
    rates: dict,
    rp: str,
    registry: set[str] | None,
    caveat_ids: set[str] | None,
    errors: Errors,
) -> None:
    """Decision pricing row: tokensmash rate fields when per-token, plus free output,
    per-request fees, and raw vendor units kept next to a sourced USD conversion."""
    allowed = set(RATE_FIELDS) | {
        "valid_from", "valid_until", "confidence", "post_valid_until",
        "billing_basis", "output_free", "per_request_usd", "min_request_usd",
        "per_decision_usd", "raw_units", "usd_conversion", "source_id", "caveat_ids", "notes",
        "cache_rates_status",
    }
    extra = set(rates) - allowed
    if extra:
        errors.add(rp, f"unknown fields: {sorted(extra)}")
    basis = rates.get("billing_basis")
    if basis not in DECISION_BILLING_BASES:
        errors.add(rp, f"billing_basis must be one of {sorted(DECISION_BILLING_BASES)}")
    if basis == "per_token":
        for f in RATE_FIELDS:
            if f not in rates:
                errors.add(rp, f"per_token row missing {f}")
    status = rates.get("cache_rates_status")
    if status is not None:
        if not isinstance(status, dict) or set(status) - {"cache_read", "cache_write"}:
            errors.add(rp, "cache_rates_status must be an object with cache_read/cache_write only")
            status = None
        else:
            for sk, sv in status.items():
                if sv not in CACHE_STATUSES:
                    errors.add(rp, f"cache_rates_status.{sk} must be one of {sorted(CACHE_STATUSES)}")
    for f, sk in (("cache_read_per_m", "cache_read"), ("cache_write_per_m", "cache_write")):
        # Decision pack: a cache rate is a published number or null with a status; never inferred.
        if f in rates and rates[f] is None:
            if not isinstance(status, dict) or status.get(sk) not in ("not_published", "vendor_states_none_charged"):
                errors.add(rp, f"{f} null requires cache_rates_status.{sk} not_published or vendor_states_none_charged")
        elif f in rates and isinstance(status, dict) and status.get(sk) in ("not_published", "vendor_states_none_charged"):
            errors.add(rp, f"{f} must be null when cache_rates_status.{sk} is {status[sk]}")
    for f in (*RATE_FIELDS, "per_request_usd", "min_request_usd", "per_decision_usd"):
        if f in rates and rates[f] is None and f in ("cache_read_per_m", "cache_write_per_m"):
            continue
        if f in rates and (not _is_number(rates[f]) or rates[f] < 0):
            errors.add(rp, f"{f} must be a number >= 0")
    if "output_free" in rates and not isinstance(rates["output_free"], bool):
        errors.add(rp, "output_free must be a boolean")
    if rates.get("output_free") is True and rates.get("output_per_m", 0) != 0:
        errors.add(rp, "output_free=true requires output_per_m == 0 (or absent)")
    if basis == "per_request" and "per_request_usd" not in rates:
        errors.add(rp, "billing_basis per_request requires per_request_usd")
    if basis == "per_decision" and "per_decision_usd" not in rates:
        errors.add(rp, "billing_basis per_decision requires per_decision_usd")
    raw = rates.get("raw_units")
    conv = rates.get("usd_conversion")
    if basis == "raw_units" and raw is None:
        errors.add(rp, "billing_basis raw_units requires raw_units")
    if raw is not None:
        if not isinstance(raw, dict) or not isinstance(raw.get("unit"), str) or not raw.get("unit"):
            errors.add(rp, "raw_units must be an object with a unit name")
        else:
            for f in ("input_per_m", "output_per_m", "per_request"):
                if f in raw and (not _is_number(raw[f]) or raw[f] < 0):
                    errors.add(rp, f"raw_units.{f} must be a number >= 0")
            if set(raw) - {"unit", "input_per_m", "output_per_m", "per_request", "notes"}:
                errors.add(rp, "raw_units has unknown fields")
        usd_present = any(f in rates for f in (*RATE_FIELDS, "per_request_usd", "per_decision_usd"))
        if usd_present and conv is None:
            errors.add(rp, "USD rates next to raw_units need usd_conversion (with a source)")
    if conv is not None:
        if not isinstance(conv, dict) or not _is_number(conv.get("usd_per_unit")) or conv["usd_per_unit"] < 0:
            errors.add(rp, "usd_conversion.usd_per_unit must be a number >= 0")
        elif not isinstance(conv.get("source_id"), str):
            errors.add(rp, "usd_conversion.source_id required")
        else:
            check_row_source_id(conv, rp + ".usd_conversion", registry or set(), errors)
        if raw is None:
            errors.add(rp, "usd_conversion requires raw_units")
    if registry is not None:
        check_row_source_id(rates, rp, registry, errors)
    check_caveat_ids(rates, rp, caveat_ids, errors)


def validate_pricing(
    path: Path,
    errors: Errors,
    registry: set[str] | None = None,
    model_registry: dict[str, str] | None = None,
    model_status: dict[str, str] | None = None,
    profile: PackProfile = MODEL_CATALOG,
    caveat_ids: set[str] | None = None,
) -> None:
    data = load_json(path, errors)
    if not isinstance(data, dict):
        return
    p = rel(path)
    require_keys(
        data,
        ("id", "schema_version", "kind", "agent", "retrieved_at", "source_urls", "models", "match"),
        p,
        errors,
    )
    if data.get("schema_version") != 1:
        errors.add(p, "schema_version must be 1")
    kinds = PRICING_KINDS if not profile.decision else frozenset({"api_usd"})
    if data.get("kind") not in kinds:
        errors.add(p, f"kind must be one of {sorted(kinds)}")
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
        if profile.decision:
            check_decision_price_row(rates, rp, registry, caveat_ids, errors)
        else:
            for f in RATE_FIELDS:
                if f not in rates:
                    errors.add(rp, f"missing {f}")
                elif not isinstance(rates[f], (int, float)) or isinstance(rates[f], bool):
                    errors.add(rp, f"{f} must be a number")
                elif rates[f] < 0:
                    errors.add(rp, f"{f} must be >= 0")
            allowed = set(RATE_FIELDS) | {"valid_from", "valid_until", "confidence", "post_valid_until", "long_prompt"}
            extra = set(rates) - allowed
            if extra:
                errors.add(rp, f"unknown fields: {sorted(extra)}")
            long_prompt = rates.get("long_prompt")
            if long_prompt is not None:
                threshold = long_prompt.get("above_prompt_tokens") if isinstance(long_prompt, dict) else None
                if not isinstance(long_prompt, dict) or set(long_prompt) != set(RATE_FIELDS) | {"above_prompt_tokens"}:
                    errors.add(rp, "long_prompt must contain above_prompt_tokens and exactly the four token rates")
                elif not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 1:
                    errors.add(rp, "long_prompt.above_prompt_tokens must be a positive integer")
                elif any(not _is_number(long_prompt[f]) or long_prompt[f] < 0 for f in RATE_FIELDS):
                    errors.add(rp, "long_prompt rates must be nonnegative numbers")
        for vk in ("valid_from", "valid_until"):
            if vk in rates and not DATE_RE.match(str(rates[vk])):
                errors.add(rp, f"{vk} must be YYYY-MM-DD")
        if "confidence" in rates and rates["confidence"] not in ("high", "medium", "low"):
            errors.add(rp, "confidence must be high|medium|low")
        future = rates.get("post_valid_until")
        if future is not None:
            if "valid_until" not in rates:
                errors.add(rp, "post_valid_until requires valid_until")
            if not isinstance(future, dict) or set(future) != set(RATE_FIELDS):
                errors.add(rp, "post_valid_until must contain exactly the four token rates")
            elif any(not isinstance(future[f], (int, float)) or isinstance(future[f], bool)
                     or future[f] < 0 for f in RATE_FIELDS):
                errors.add(rp, "post_valid_until rates must be nonnegative numbers")
        vu = rates.get("valid_until")
        if isinstance(vu, str) and DATE_RE.match(vu):
            try:
                is_historical = False
                if model_registry is not None and model_status is not None:
                    canonical = resolve_model_id(mid, model_registry)
                    if canonical is not None and model_status.get(canonical) == "historical":
                        is_historical = True
                if not is_historical and date.fromisoformat(vu) < date.today():
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
    profile: PackProfile = MODEL_CATALOG,
    caveat_ids: set[str] | None = None,
    metric_units: dict[str, str] | None = None,
) -> None:
    data = load_json(path, errors)
    if not isinstance(data, dict):
        return
    p = rel(path)
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
    if profile.perf_providers is None:
        if not isinstance(data.get("provider"), str) or not SLUG_RE.match(data["provider"]):
            errors.add(p, "provider must be a lowercase slug (publisher of the claims/scores)")
    elif data.get("provider") not in profile.perf_providers:
        errors.add(p, f"provider must be one of {sorted(profile.perf_providers)}")
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
                check_caveat_ids(c, cp, caveat_ids, errors)

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
                if s.get("unit") not in profile.perf_units and "unit" in s:
                    errors.add(sp, f"unit must be one of {sorted(profile.perf_units)}")
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
                check_caveat_ids(s, sp, caveat_ids, errors)
                if profile.decision:
                    check_decision_score(s, sp, metric_units, errors)

    if "missing" in data and not isinstance(data["missing"], list):
        errors.add(p, "missing must be an array")



def check_decision_score(s: dict, sp: str, metric_units: dict[str, str] | None, errors: Errors) -> None:
    """Decision score rows must name a registered metric whose unit_default equals the row unit;
    latency and cost rows (decided by the METRIC's unit, never by the row's own unit label) must
    say who measured, from where, and at which percentile (a bare ms number is not evidence)."""
    mid = s.get("metric_id")
    if not isinstance(mid, str):
        errors.add(sp, "decision score rows require metric_id (registered in metrics.json)")
    unit = s.get("unit")
    meas = s.get("measurement")
    if meas is not None:
        if not isinstance(meas, dict):
            errors.add(sp, "measurement must be an object")
            meas = None
        else:
            known = {"measured_by", "vantage", "hardware", "region", "percentile",
                     "input_tokens", "questions_per_request", "prefix_cache", "n", "published_by"}
            if set(meas) - known:
                errors.add(sp, f"measurement has unknown fields: {sorted(set(meas) - known)}")
            if "vantage" in meas and meas["vantage"] not in LATENCY_VANTAGES:
                errors.add(sp, f"measurement.vantage must be one of {sorted(LATENCY_VANTAGES)}")
            if "percentile" in meas and meas["percentile"] not in LATENCY_PERCENTILES:
                errors.add(sp, f"measurement.percentile must be one of {sorted(LATENCY_PERCENTILES)}")
            if "prefix_cache" in meas and meas["prefix_cache"] is not None and not isinstance(meas["prefix_cache"], bool):
                errors.add(sp, "measurement.prefix_cache must be boolean or null")
            for k in ("input_tokens", "questions_per_request", "n"):
                if k in meas and (not isinstance(meas[k], int) or isinstance(meas[k], bool) or meas[k] < 0):
                    errors.add(sp, f"measurement.{k} must be a non-negative integer")
    metric_unit = metric_units.get(mid) if (metric_units is not None and isinstance(mid, str)) else None
    if metric_unit is not None and unit != metric_unit:
        errors.add(sp, f"row unit {unit!r} must equal metric {mid!r} unit_default {metric_unit!r}")
    # The registered metric decides what context is required; the row unit is checked above.
    required_unit = metric_unit if metric_unit is not None else unit
    if required_unit == "ms":
        for k in ("measured_by", "vantage", "percentile"):
            if not isinstance(meas, dict) or k not in meas:
                errors.add(sp, f"latency row (unit ms) requires measurement.{k}")
    if required_unit == "usd_per_decision":
        if not isinstance(meas, dict) or "measured_by" not in meas:
            errors.add(sp, "cost row (unit usd_per_decision) requires measurement.measured_by")


def validate_capabilities(
    path: Path,
    errors: Errors,
    registry: set[str] | None = None,
    model_registry: dict[str, str] | None = None,
    profile: PackProfile = MODEL_CATALOG,
    caveat_ids: set[str] | None = None,
) -> None:
    data = load_json(path, errors)
    if not isinstance(data, dict):
        return
    p = rel(path)
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
        if profile.decision:
            check_decision_surface(s, sp, registry, model_registry, caveat_ids, errors)
            continue
        for k in ("provider", "model", "surface", "valid_efforts", "unsupported_behavior"):
            if k not in s:
                errors.add(sp, f"missing {k}")
        if model_registry is not None and isinstance(s.get("model"), str):
            check_model_id(s["model"], sp, model_registry, errors)
        ve = s.get("valid_efforts")
        if not isinstance(ve, list):
            errors.add(sp, "valid_efforts must be an array")



def check_decision_surface(
    s: dict,
    sp: str,
    registry: set[str] | None,
    model_registry: dict[str, str] | None,
    caveat_ids: set[str] | None,
    errors: Errors,
) -> None:
    """Decision-interface surface: what a model exposes on one serving surface."""
    for k in ("model", "surface"):
        if k not in s:
            errors.add(sp, f"missing {k}")
    if s.get("surface") not in DECISION_SURFACES:
        errors.add(sp, f"surface must be one of {sorted(DECISION_SURFACES)}")
    if model_registry is not None and isinstance(s.get("model"), str):
        check_model_id(s["model"], sp, model_registry, errors)
    qt = s.get("question_types")
    if qt is not None and (not isinstance(qt, list) or not all(isinstance(x, str) and SLUG_RE.match(x) for x in qt)):
        errors.add(sp, "question_types must be an array of lowercase slugs (choice, score, noul, binary, ...)")
    for k in ("abstain_supported", "returns_probabilities", "calibration_claimed", "rationale_supported",
              "output_tokens_billed"):
        if k in s and s[k] is not None and not isinstance(s[k], bool):
            errors.add(sp, f"{k} must be boolean or null (null = unknown)")
    if "max_options" in s and s["max_options"] is not None and (
        not isinstance(s["max_options"], int) or isinstance(s["max_options"], bool) or s["max_options"] < 1
    ):
        errors.add(sp, "max_options must be a positive integer or null")
    ctx = s.get("context")
    if ctx is not None:
        if not isinstance(ctx, dict) or set(ctx) - {"state_tokens", "total_tokens"}:
            errors.add(sp, "context must be an object with state_tokens and/or total_tokens")
        else:
            for k, v in ctx.items():
                if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 1):
                    errors.add(sp, f"context.{k} must be a positive integer or null")
    qpr = s.get("questions_per_request")
    if qpr is not None:
        if not isinstance(qpr, dict) or set(qpr) - {"max", "fan_out_supported", "packing_supported"}:
            errors.add(sp, "questions_per_request must be an object (max, fan_out_supported, packing_supported)")
        else:
            m = qpr.get("max")
            if m is not None and (not isinstance(m, int) or isinstance(m, bool) or m < 1):
                errors.add(sp, "questions_per_request.max must be a positive integer or null")
            for k in ("fan_out_supported", "packing_supported"):
                if qpr.get(k) is not None and not isinstance(qpr.get(k), bool):
                    errors.add(sp, f"questions_per_request.{k} must be boolean or null")
    if registry is not None:
        check_source_ids(s, sp, registry, errors, require_doc=False)
    check_caveat_ids(s, sp, caveat_ids, errors)


def load_model_registry(
    pack_dir: Path, errors: Errors, profile: PackProfile = MODEL_CATALOG
) -> tuple[dict[str, str], dict[str, str]]:
    """Return (resolve, status) maps. resolve: id/alias -> canonical id.
    status: canonical id -> status (e.g. "ga", "historical"). Empty if models.json missing.
    """
    path = pack_dir / "models.json"
    p = rel(path)
    if not path.is_file():
        errors.add(p, "models.json missing — L0 model registry required")
        return {}, {}
    data = load_json(path, errors)
    if not isinstance(data, dict):
        return {}, {}
    require_keys(data, ("id", "schema_version", "generated_at", "models"), p, errors)
    if data.get("schema_version") != 1:
        errors.add(p, "schema_version must be 1")
    if data.get("id") != f"{profile.name}-models":
        errors.add(p, f"id must be '{profile.name}-models'")
    if not DATE_RE.match(str(data.get("generated_at", ""))):
        errors.add(p, "generated_at must be YYYY-MM-DD")
    models = data.get("models")
    resolve: dict[str, str] = {}
    status: dict[str, str] = {}
    if not isinstance(models, list) or (not models and not profile.allow_empty):
        errors.add(p, "models must be a non-empty array")
        return resolve, status
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
        if isinstance(m.get("status"), str):
            status[mid] = m["status"]
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
    return resolve, status


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


def load_caveats(
    pack_dir: Path,
    errors: Errors,
    profile: PackProfile,
    source_registry: set[str],
    model_registry: dict[str, str],
    metric_ids: set[str],
) -> set[str]:
    """Validate caveats.json (decision packs) and return the set of caveat ids."""
    path = pack_dir / "caveats.json"
    p = rel(path)
    ids: set[str] = set()
    if not path.is_file():
        errors.add(p, "caveats.json missing — nuance registry required")
        return ids
    data = load_json(path, errors)
    if not isinstance(data, dict):
        return ids
    require_keys(data, ("id", "schema_version", "generated_at", "caveats"), p, errors)
    if data.get("schema_version") != 1:
        errors.add(p, "schema_version must be 1")
    if data.get("id") != f"{profile.name}-caveats":
        errors.add(p, f"id must be '{profile.name}-caveats'")
    if not DATE_RE.match(str(data.get("generated_at", ""))):
        errors.add(p, "generated_at must be YYYY-MM-DD")
    caveats = data.get("caveats")
    if not isinstance(caveats, list):
        errors.add(p, "caveats must be an array")
        return ids
    for i, c in enumerate(caveats):
        cp = f"{p}#caveats[{i}]"
        if not isinstance(c, dict):
            errors.add(cp, "must be an object")
            continue
        require_keys(c, ("id", "statement", "affects", "strength", "implication", "evidence"), cp, errors)
        cid = c.get("id")
        if not isinstance(cid, str) or not SOURCE_ID_RE.match(cid):
            errors.add(cp, f"invalid caveat id {cid!r}")
        elif cid in ids:
            errors.add(cp, f"duplicate caveat id {cid!r}")
        else:
            ids.add(cid)
        for k in ("statement", "implication"):
            if k in c and (not isinstance(c[k], str) or not c[k].strip()):
                errors.add(cp, f"{k} must be a non-empty string")
        if c.get("strength") not in CAVEAT_STRENGTHS:
            errors.add(cp, f"strength must be one of {sorted(CAVEAT_STRENGTHS)}")
        affects = c.get("affects")
        if not isinstance(affects, dict) or set(affects) - {"models", "metrics", "sources"}:
            errors.add(cp, "affects must be an object with models[], metrics[], sources[]")
        else:
            if not any(isinstance(affects.get(k), list) and affects[k] for k in ("models", "metrics", "sources")):
                errors.add(cp, "affects must name at least one model, metric, or source")
            for mid in affects.get("models") or []:
                if not isinstance(mid, str) or resolve_model_id(mid, model_registry) is None:
                    errors.add(cp, f"affects.models entry {mid!r} not in models.json or baseline (id or alias)")
            for mid in affects.get("metrics") or []:
                if mid not in metric_ids:
                    errors.add(cp, f"affects.metrics entry {mid!r} not in metrics.json")
            for sid in affects.get("sources") or []:
                if sid not in source_registry:
                    errors.add(cp, f"affects.sources entry {sid!r} not in SOURCES.json")
        ev = c.get("evidence")
        if not isinstance(ev, list) or not ev:
            errors.add(cp, "evidence must be a non-empty array of {source_id, quote}")
        else:
            for j, e in enumerate(ev):
                ep = f"{cp}.evidence[{j}]"
                if not isinstance(e, dict) or set(e) - {"source_id", "quote", "location"}:
                    errors.add(ep, "must be an object with source_id, quote, optional location")
                    continue
                if not isinstance(e.get("quote"), str) or not e["quote"].strip():
                    errors.add(ep, "quote required (verbatim)")
                if e.get("source_id") not in source_registry:
                    errors.add(ep, f"source_id {e.get('source_id')!r} not in SOURCES.json")
    return ids


def validate_decision_models(
    pack_dir: Path,
    errors: Errors,
    baseline_resolve: dict[str, str],
    caveat_ids: set[str],
) -> None:
    """Decision-model fields on local models.json entries, plus the no-duplicate-baseline rule."""
    data = load_json(pack_dir / "models.json", Errors())
    if not isinstance(data, dict):
        return
    for i, m in enumerate(data.get("models") or []):
        if not isinstance(m, dict):
            continue
        mp = rel(pack_dir / "models.json") + f"#models[{i}]"
        for name in [m.get("id"), *(m.get("aliases") or [])]:
            if isinstance(name, str) and name in baseline_resolve:
                errors.add(mp, f"{name!r} already exists in the baseline model-catalog; reference it, do not duplicate")
        if m.get("status") not in DECISION_MODEL_STATUSES:
            errors.add(mp, f"status must be one of {sorted(DECISION_MODEL_STATUSES)}")
        if m.get("weights") not in DECISION_WEIGHTS:
            errors.add(mp, "weights must be 'open' or 'closed'")
        for k in ("license", "base_model", "hf_repo"):
            if k in m and m[k] is not None and (not isinstance(m[k], str) or not m[k].strip()):
                errors.add(mp, f"{k} must be a non-empty string or null")
        for k in ("params_total", "params_active"):
            if k in m and m[k] is not None and (not _is_number(m[k]) or m[k] <= 0):
                errors.add(mp, f"{k} must be a positive number or null")
        pt, pa = m.get("params_total"), m.get("params_active")
        if _is_number(pt) and _is_number(pa) and pa > pt:
            errors.add(mp, "params_active must not exceed params_total")
        surf = m.get("surfaces")
        if surf is not None:
            if not isinstance(surf, list) or any(x not in DECISION_SURFACES for x in surf):
                errors.add(mp, f"surfaces must be an array drawn from {sorted(DECISION_SURFACES)}")
        check_caveat_ids(m, mp, caveat_ids, errors)


def validate_catalog_pack(
    pack_dir: Path,
    errors: Errors,
    profile: PackProfile = MODEL_CATALOG,
    baseline_dir: Path | None = None,
) -> None:
    """Shared validation for model-catalog and decision-model-catalog.

    baseline_dir overrides where the fallback models.json is read from (tests).
    """
    validate_pack_envelope(pack_dir, errors, strict_files=profile.strict_files)
    source_registry = load_source_registry(pack_dir, errors, profile)
    model_registry, model_status = load_model_registry(pack_dir, errors, profile)
    baseline_resolve: dict[str, str] = {}
    if profile.baseline:
        bdir = baseline_dir or (DATA / profile.baseline)
        if (bdir / "models.json").is_file():
            baseline_resolve, baseline_status = load_model_registry(bdir, Errors())
            # Local entries win; baseline fills in general-LLM ids and aliases.
            model_registry = {**baseline_resolve, **model_registry}
            model_status = {**baseline_status, **model_status}
        else:
            errors.add(rel(pack_dir), f"baseline {profile.baseline}/models.json not found for model fallback")
    model_data = load_json(pack_dir / "models.json", Errors())
    if isinstance(model_data, dict):
        for i, model in enumerate(model_data.get("models") or []):
            if not isinstance(model, dict) or "released" not in model:
                continue
            mp = rel((pack_dir / "models.json")) + f"#models[{i}]"
            if not DATE_RE.match(str(model.get("released", ""))):
                errors.add(mp, "released must be YYYY-MM-DD")
            sid = model.get("release_source_id")
            if not isinstance(sid, str) or sid not in source_registry:
                errors.add(mp, "released requires release_source_id from SOURCES.json")
    metric_ids: set[str] = set()
    metric_units: dict[str, str] = {}
    mpath = pack_dir / "metrics.json"
    if mpath.is_file():
        mdata = load_json(mpath, Errors())  # soft — full check later
        if isinstance(mdata, dict):
            for m in mdata.get("metrics") or []:
                if isinstance(m, dict) and m.get("id"):
                    metric_ids.add(str(m["id"]))
                    if isinstance(m.get("unit_default"), str):
                        metric_units[str(m["id"])] = m["unit_default"]
    caveat_ids: set[str] | None = None
    if profile.decision:
        caveat_ids = load_caveats(pack_dir, errors, profile, source_registry, model_registry, metric_ids)
        validate_decision_models(pack_dir, errors, baseline_resolve, caveat_ids)
    pricing_dir = pack_dir / "pricing"
    perf_dir = pack_dir / "performance"
    if pricing_dir.is_dir():
        paths = sorted(pricing_dir.glob("*.json"))
        if not paths and not profile.allow_empty:
            errors.add(rel(pack_dir), "pricing/ has no JSON tables")
        for path in paths:
            validate_pricing(path, errors, source_registry, model_registry, model_status, profile, caveat_ids)
    elif not profile.allow_empty:
        errors.add(rel(pack_dir), "missing pricing/")
    if perf_dir.is_dir():
        for path in sorted(perf_dir.glob("*.json")):
            validate_performance(path, errors, source_registry, model_registry, metric_ids, profile, caveat_ids,
                                 metric_units if profile.decision else None)
    cap_dir = pack_dir / "capabilities"
    if cap_dir.is_dir():
        for path in sorted(cap_dir.glob("*.json")):
            validate_capabilities(path, errors, source_registry, model_registry, profile, caveat_ids)


    # Comparability: metric_ids that mix source_type or harness must set comparable=false on rows
    by_mid: dict[str, list[tuple[str, str, str | None, object]]] = {}
    perf_paths = sorted((pack_dir / "performance").glob("*.json")) if (pack_dir / "performance").is_dir() else []
    for path in perf_paths:
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
                    rel(path) + f"#scores[{i}]",
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

    # Board and vendor-table scores carry an explicit source snapshot. This makes
    # snapshot/group consistency checkable across files and ingestion lanes.
    by_snapshot_metric: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for path in sorted((pack_dir / "performance").glob("*.json")) if (pack_dir / "performance").is_dir() else []:
        data = load_json(path, Errors())
        if not isinstance(data, dict):
            continue
        for i, row in enumerate(data.get("scores") or []):
            if not isinstance(row, dict):
                continue
            metric_id = row.get("metric_id")
            snapshot_id = row.get("snapshot_id")
            group = row.get("comparability_group")
            source_type = row.get("source_type")
            loc = rel(path) + f"#scores[{i}]"
            if source_type in {"third_party_board", "vendor_table"}:
                if not isinstance(snapshot_id, str) or not snapshot_id.strip():
                    errors.add(loc, f"{source_type} score row requires snapshot_id")
                if not isinstance(metric_id, str) or not metric_id.strip():
                    errors.add(loc, f"{source_type} score row requires metric_id")
                if not isinstance(group, str) or not group.strip():
                    errors.add(loc, f"{source_type} score row requires comparability_group")
            if not isinstance(metric_id, str) or not isinstance(snapshot_id, str) or not isinstance(group, str):
                continue
            by_snapshot_metric.setdefault((snapshot_id, metric_id), []).append((loc, group))
    for (snapshot_id, metric_id), rows in by_snapshot_metric.items():
        groups = {group for _, group in rows}
        if len(groups) > 1:
            for loc, group in rows:
                errors.add(
                    loc,
                    f"snapshot {snapshot_id!r} metric_id {metric_id!r} uses multiple "
                    f"comparability_groups {sorted(groups)!r} (row has {group!r})",
                )

    for name in profile.required_schemas:
        if not (pack_dir / "schemas" / name).is_file():
            errors.add(rel(pack_dir), f"missing schemas/{name}")
    if not (pack_dir / "SCHEMA.md").is_file() and not (pack_dir / "schemas" / "README.md").is_file():
        errors.add(rel(pack_dir), "missing SCHEMA.md or schemas/README.md")
    # metrics.json optional but if present must be well-formed
    mpath = pack_dir / "metrics.json"
    if mpath.is_file():
        mdata = load_json(mpath, errors)
        mp = rel(mpath)
        if isinstance(mdata, dict):
            require_keys(mdata, ("id", "schema_version", "generated_at", "metrics"), mp, errors)
            if mdata.get("id") != f"{profile.name}-metrics":
                errors.add(mp, f"id must be '{profile.name}-metrics'")
            if not isinstance(mdata.get("metrics"), list) or (not mdata["metrics"] and not profile.allow_empty):
                errors.add(mp, "metrics must be non-empty array")
            elif profile.decision:
                seen_m: set[str] = set()
                for k, m in enumerate(mdata["metrics"]):
                    if not isinstance(m, dict) or not isinstance(m.get("id"), str):
                        errors.add(f"{mp}#metrics[{k}]", "id required")
                    elif m["id"] in seen_m:
                        errors.add(f"{mp}#metrics[{k}]", f"duplicate metric id {m['id']!r}")
                    else:
                        seen_m.add(m["id"])
    elif profile.decision:
        errors.add(rel(pack_dir), "metrics.json missing")


def validate_model_catalog(pack_dir: Path, errors: Errors) -> None:
    validate_catalog_pack(pack_dir, errors, MODEL_CATALOG)


def validate_decision_model_catalog(
    pack_dir: Path, errors: Errors, baseline_dir: Path | None = None
) -> None:
    validate_catalog_pack(pack_dir, errors, DECISION_CATALOG, baseline_dir)



def validate_policy_pack(pack_dir: Path, errors: Errors) -> None:
    """Validate model-choice-policy: envelope, ops integrity, catalog pin, evidence refs."""
    envelope = validate_pack_envelope(pack_dir, errors)
    path = pack_dir / "operating-points.json"
    p = rel(path)
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
                    rel((pack_dir / "pack.json")),
                    f"related.catalog {rel_cat!r} != operating-points catalog_ref {catalog_ref!r}",
                )
        # pack tag vs policy_version
        tag = str(envelope.get("tag", ""))
        pv = str(data.get("policy_version", ""))
        if tag and pv and not tag.endswith(f"-v{pv}"):
            errors.add(
                rel((pack_dir / "pack.json")),
                f"tag {tag!r} should end with -v{pv} (policy_version)",
            )

    # Load pinned catalog if present on disk (same repo)
    catalog_dir = DATA / "model-catalog"
    model_registry: dict[str, str] = {}
    source_ids: set[str] = set()
    catalog_file_ids: set[str] = set()  # stem of pricing/performance json
    effort_surfaces: list[dict] = []
    if catalog_dir.is_dir():
        model_registry, _ = load_model_registry(catalog_dir, Errors())  # soft: don't double-count models.json errors
        # re-load models without polluting if already validated; if empty, try direct
        if not model_registry and (catalog_dir / "models.json").is_file():
            model_registry, _ = load_model_registry(catalog_dir, errors)
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
            errors.add(f"{rel(instance_path)}[{loc}]", err.message)

    check(DATA / "index.json", DATA / "schemas" / "index-v1.schema.json")
    packs = [pack] if pack else [d.name for d in DATA.iterdir() if (d / "pack.json").is_file()]
    for name in packs:
        pdir = DATA / name
        check(pdir / "pack.json", DATA / "schemas" / "pack-v1.schema.json")
        if name == "decision-model-catalog":
            for fname, schema in (
                ("SOURCES.json", "sources-v1"), ("models.json", "models-v1"),
                ("metrics.json", "metrics-v1"), ("caveats.json", "caveats-v1"),
            ):
                if (pdir / fname).is_file():
                    check(pdir / fname, pdir / "schemas" / f"{schema}.schema.json")
            for sub, schema in (("pricing", "pricing-v1"), ("performance", "performance-v1"),
                                ("capabilities", "capabilities-v1")):
                for path in sorted((pdir / sub).glob("*.json")):
                    check(path, pdir / "schemas" / f"{schema}.schema.json")
        elif name == "model-catalog":
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
            elif args.pack in PROFILES:
                validate_catalog_pack(pdir, errors, PROFILES[args.pack])
            elif args.pack == "model-choice-policy":
                validate_policy_pack(pdir, errors)
            else:
                validate_pack_envelope(pdir, errors)
        else:
            for pdir in sorted(DATA.iterdir()):
                if (pdir / "pack.json").is_file():
                    if pdir.name in PROFILES:
                        validate_catalog_pack(pdir, errors, PROFILES[pdir.name])
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
