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
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TAG_RE = re.compile(r"^data-[a-z][a-z0-9-]*-v\d+\.\d+\.\d+$")
PACK_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

PRICING_KINDS = frozenset({"api_usd", "codex_credits"})
PRICING_AGENTS = frozenset({"claude-code", "codex", "grok"})
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
    {"vendor_blog", "vendor_docs", "third_party_eval", "academic", "other"}
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


def validate_pricing(path: Path, errors: Errors, registry: set[str] | None = None) -> None:
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
        for f in RATE_FIELDS:
            if f not in rates:
                errors.add(rp, f"missing {f}")
            elif not isinstance(rates[f], (int, float)) or isinstance(rates[f], bool):
                errors.add(rp, f"{f} must be a number")
            elif rates[f] < 0:
                errors.add(rp, f"{f} must be >= 0")
        extra = set(rates) - set(RATE_FIELDS)
        if extra:
            errors.add(rp, f"unknown fields: {sorted(extra)}")
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
    path: Path, errors: Errors, registry: set[str] | None = None
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



def validate_capabilities(path: Path, errors: Errors, registry: set[str] | None = None) -> None:
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
        ve = s.get("valid_efforts")
        if not isinstance(ve, list) or not ve:
            errors.add(sp, "valid_efforts must be non-empty array")


def validate_model_catalog(pack_dir: Path, errors: Errors) -> None:
    validate_pack_envelope(pack_dir, errors)
    registry = load_source_registry(pack_dir, errors)
    pricing_dir = pack_dir / "pricing"
    perf_dir = pack_dir / "performance"
    if pricing_dir.is_dir():
        paths = sorted(pricing_dir.glob("*.json"))
        if not paths:
            errors.add(str(pack_dir.relative_to(REPO)), "pricing/ has no JSON tables")
        for path in paths:
            validate_pricing(path, errors, registry)
    else:
        errors.add(str(pack_dir.relative_to(REPO)), "missing pricing/")
    if perf_dir.is_dir():
        for path in sorted(perf_dir.glob("*.json")):
            validate_performance(path, errors, registry)
    cap_dir = pack_dir / "capabilities"
    if cap_dir.is_dir():
        for path in sorted(cap_dir.glob("*.json")):
            validate_capabilities(path, errors, registry)

    for name in (
        "pricing-v1.schema.json",
        "performance-v1.schema.json",
        "sources-v1.schema.json",
    ):
        if not (pack_dir / "schemas" / name).is_file():
            errors.add(str(pack_dir.relative_to(REPO)), f"missing schemas/{name}")
    if not (pack_dir / "SCHEMA.md").is_file() and not (pack_dir / "schemas" / "README.md").is_file():
        errors.add(str(pack_dir.relative_to(REPO)), "missing SCHEMA.md or schemas/README.md")


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
            else:
                validate_pack_envelope(pdir, errors)
        else:
            for pdir in sorted(DATA.iterdir()):
                if (pdir / "pack.json").is_file():
                    if pdir.name == "model-catalog":
                        validate_model_catalog(pdir, errors)
                    else:
                        validate_pack_envelope(pdir, errors)

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
