"""Structured input provenance for the owner-authorized free-route trial.

The caller supplies a manifest; the router validates it through router code. The manifest is the
only input evidence: a caller declaration never proves content scanning or OS isolation, so every
result states that limit. Credential contents are never read: paths are normalized, traversal is
collapsed, and an existing symlink is resolved with `realpath`, which stats but never opens the
target.

A manifest is one JSON object. Every entry is bound provenance: a GitHub entry names one repository,
and a local entry names the repository, the root it is read under, the path inside that root and an
explicit classification.

    {"v": 1, "inputs": [
      {"kind": "github", "repo": "tnunamak/minnows", "visibility": "private"},
      {"kind": "github", "repo": "other/repo", "visibility": "public",
       "public_provenance": "https://github.com/other/repo"},
      {"kind": "local", "repo": "tnunamak/minnows", "root": "/home/me/code/minnows",
       "path": "src/a.py", "classification": "code"}]}

`assess` returns a structured state and, for a refusal, the first router-code reason. The manifest
is `allowed` only when every input is allowed and bound. Unknown, omitted or ambiguous provenance is
never allowed, and the excluded company namespace is refused anywhere in a repository, root or path.
"""
import json
import os
import re

MANIFEST_KEYS = {"v", "inputs"}
GITHUB_KEYS = {"kind", "repo", "visibility", "public_provenance", "contains"}
LOCAL_KEYS = {"kind", "repo", "root", "path", "classification", "visibility", "public_provenance", "contains"}
KINDS = ("github", "local")
VISIBILITIES = ("public", "private")
CLASSIFICATIONS = ("code", "docs", "data", "sensitive")
# Typed content classifications that the free route always refuses.
PROHIBITED_CONTENT = ("secrets", "PDPP_data", "Jev_text", "personal_data")
MAX_INPUTS = 64

# A GitHub repo identity: owner/name, lowercased and stripped of a trailing `.git` and slashes.
OWNER = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,38})")
NAME = re.compile(r"[a-z0-9._-]{1,100}")

# Credential and secret path components, refused at any depth. `credential_path` below splits the
# normalized path, so `..` is already gone and a name is compared per component, never as a
# substring of a longer word: `oauth_client.py` is source, `oauth.txt` is a credential file.
CREDENTIAL_DIRS = {".ssh", ".aws", ".gnupg", ".docker", ".kube", ".azure"}
CREDENTIAL_NAMES = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "known_hosts", "keystore", "truststore",
                    ".netrc", ".npmrc", ".pypirc", "auth.json"}
CREDENTIAL_EXTENSIONS = (".pem", ".key", ".p12", ".pfx", ".jks", ".ppk", ".pkcs12", ".token", ".tokens",
                         ".credentials", ".secrets", ".passwords")
# Ambiguous credential words: refused unless the component is an ordinary source or docs file.
CREDENTIAL_WORDS = {"token", "tokens", "oauth", "oauth2", "apikey", "api_key", "api-key", "private_key",
                    "private-key", "secret", "secrets", "password", "passwords", "passwd", "pwd",
                    "credential", "credentials"}
SOURCE_EXTENSIONS = (".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java",
                     ".kt", ".kts", ".rb", ".php", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".cs",
                     ".swift", ".scala", ".sh", ".bash", ".zsh", ".fish", ".ps1", ".sql", ".html",
                     ".htm", ".css", ".scss", ".sass", ".less", ".md", ".mdx", ".rst", ".adoc", ".vue",
                     ".svelte", ".ex", ".exs", ".erl", ".clj", ".lua", ".pl", ".r", ".jl", ".dart",
                     ".groovy", ".tf", ".proto", ".graphql", ".gql")

DEFAULT_POLICY = {
    "allowed_github_owners": ["tnunamak"],
    "excluded_github_owners": ["vana-com"],
    "other_owners": "public_with_explicit_provenance",
    "prohibited_content": list(PROHIBITED_CONTENT),
    "credential_paths_denied": True,
}
# A data edit must not be able to relax the company-namespace denial, so the floor is code-fixed and
# the pack's list is unioned with it. `check_policy` reports a pack that drops one.
MANDATORY_EXCLUDED_OWNERS = ("vana-com",)
CALLER_ASSERTION_LIMIT = (
    "structured caller declarations only: they do not prove content scanning, verify repository "
    "visibility, prove that credential paths are the only sensitive files, read no credential "
    "contents, and give no runtime or OS isolation guarantee"
)


def policy_view(input_policy):
    """The effective input policy: the pack's `input_policy` merged over the fixed defaults.

    The company-namespace denial is unioned with the code-fixed floor, so removing an owner from the
    pack cannot re-open it: a data edit can only add exclusions, never relax one.
    """
    merged = dict(DEFAULT_POLICY)
    if isinstance(input_policy, dict):
        merged.update({k: v for k, v in input_policy.items() if k in DEFAULT_POLICY})
    merged["excluded_github_owners"] = sorted(set(merged["excluded_github_owners"]) | set(MANDATORY_EXCLUDED_OWNERS))
    return merged


def check_policy(input_policy):
    """Validate the optional pack `input_policy` object. Absent is valid and means the defaults."""
    if input_policy is None:
        return []
    if not isinstance(input_policy, dict):
        return ["input_policy must be an object"]
    errors = []
    unknown = sorted(set(input_policy) - set(DEFAULT_POLICY) - {"caller_assertion_limit"})
    if unknown:
        errors.append(f"input_policy: unknown keys {', '.join(unknown)}")
    for key in ("allowed_github_owners", "excluded_github_owners", "prohibited_content"):
        if key in input_policy and not (isinstance(input_policy[key], list) and input_policy[key]
                                        and all(isinstance(v, str) and v for v in input_policy[key])):
            errors.append(f"input_policy.{key} must be a non-empty list of strings")
    owners = (input_policy.get("allowed_github_owners") or []) + (input_policy.get("excluded_github_owners") or [])
    if any(not OWNER.fullmatch(owner) for owner in owners if isinstance(owner, str)):
        errors.append("input_policy owners must be lowercase GitHub owner names")
    declined = set(input_policy.get("excluded_github_owners") or [])
    if "excluded_github_owners" in input_policy and not set(MANDATORY_EXCLUDED_OWNERS) <= declined:
        errors.append("input_policy.excluded_github_owners must keep the company namespace "
                      + ", ".join(MANDATORY_EXCLUDED_OWNERS) + " denied")
    if "other_owners" in input_policy and input_policy["other_owners"] != "public_with_explicit_provenance":
        errors.append("input_policy.other_owners must be public_with_explicit_provenance")
    if "credential_paths_denied" in input_policy and input_policy["credential_paths_denied"] is not True:
        errors.append("input_policy.credential_paths_denied must be true")
    if isinstance(input_policy.get("prohibited_content"), list):
        if not set(PROHIBITED_CONTENT) <= set(input_policy["prohibited_content"]):
            errors.append("input_policy.prohibited_content must include secrets, PDPP_data, Jev_text and personal_data")
    return errors


def canonical_repo(value):
    """(owner, name) canonical identity, or None. Case is lowered; a trailing `.git` and slashes are stripped."""
    if not isinstance(value, str):
        return None
    text = value.strip().rstrip("/")
    if text.endswith(".git"):
        text = text[:-4]
    owner, sep, name = text.partition("/")
    if not sep or "/" in name:
        return None
    owner, name = owner.lower(), name.lower()
    if not OWNER.fullmatch(owner) or not NAME.fullmatch(name):
        return None
    return owner, name


def normalize_root(value):
    """The declared root, normalized, or None when it is not a usable directory path."""
    if not isinstance(value, str) or not value.strip() or "\0" in value or "\n" in value:
        return None
    return os.path.normpath(value.strip().replace("\\", "/")).replace("\\", "/")


def normalize_path(value):
    """(normalized, None) or (None, reason). A local path is relative to its declared root."""
    if not isinstance(value, str) or not value.strip() or "\0" in value or "\n" in value:
        return None, "invalid_path"
    text = value.strip().replace("\\", "/")
    if text.startswith("/") or (len(text) > 1 and text[1] == ":"):
        return None, "invalid_path"
    normalized = os.path.normpath(text).replace("\\", "/")
    if normalized in (".", "..") or normalized.startswith("../") or "/../" in normalized:
        return None, "path_traversal"
    return normalized, None


def resolved(root_normalized, path_normalized):
    """(root_real, target_real) after symlink resolution. `realpath` stats a target and never opens it."""
    root_abs = os.path.abspath(root_normalized)
    target = os.path.join(root_abs, path_normalized)
    return os.path.realpath(root_abs), os.path.realpath(target)


def within(root_real, target_real):
    """A target is inside the root only when it is a strict descendant, so `/a/b` never contains `/a/bc`."""
    return target_real.startswith(root_real.rstrip("/") + "/")


def credential_path(text):
    """True when any path component is a credential or secret name or extension."""
    parts = [part for part in text.replace("\\", "/").split("/") if part not in ("", ".")]
    for index, part in enumerate(parts):
        lowered = part.lower()
        stem = lowered.split(".", 1)[0]
        if (lowered in CREDENTIAL_DIRS or lowered in CREDENTIAL_NAMES or stem in CREDENTIAL_NAMES
                or lowered == ".env" or lowered.startswith(".env.")):
            return True
        if any(lowered.endswith(extension) for extension in CREDENTIAL_EXTENSIONS):
            return True
        if stem in CREDENTIAL_WORDS and lowered[len(stem):] not in SOURCE_EXTENSIONS:
            return True
        if lowered == ".git" and parts[index + 1:index + 2] == ["config"]:
            return True
    return False


def excluded_namespace(text, excluded_owners):
    """The excluded company namespace anywhere in a repository, root or path, conservatively."""
    lowered = text.lower()
    return any(owner and owner in lowered for owner in excluded_owners)


def _assess_github(entry, policy):
    owner_name = canonical_repo(entry.get("repo"))
    if owner_name is None:
        return "github_repo_invalid"
    owner = owner_name[0]
    if owner in policy["excluded_github_owners"]:
        return "company_private_namespace"
    if owner in policy["allowed_github_owners"]:
        return None
    if entry.get("visibility") not in ("public",):
        return "other_namespace_requires_public"
    if not (isinstance(entry.get("public_provenance"), str) and entry["public_provenance"].strip()):
        return "public_provenance_required"
    return None


def _assess_local(entry, policy):
    """(identity, reason). Every local input is bound to one authorized repository, root and path."""
    identity = {"kind": "local", "repo": None, "root": None, "path": None, "visibility": entry.get("visibility"),
                "classification": entry.get("classification") or None}
    repo = entry.get("repo")
    owner_name = canonical_repo(repo) if isinstance(repo, str) else None
    if owner_name is None:
        return identity, "repo_binding_required"
    identity["repo"] = owner_name
    owner = owner_name[0]
    if owner in policy["excluded_github_owners"]:
        return identity, "company_private_namespace"
    if owner not in policy["allowed_github_owners"]:
        if entry.get("visibility") != "public":
            return identity, "other_namespace_requires_public"
        if not (isinstance(entry.get("public_provenance"), str) and entry["public_provenance"].strip()):
            return identity, "public_provenance_required"
    root = normalize_root(entry.get("root"))
    if root is None:
        return identity, "invalid_root"
    identity["root"] = root
    path, refusal = normalize_path(entry.get("path"))
    if refusal:
        return identity, refusal
    identity["path"] = path
    root_real, target_real = resolved(root, path)
    if not within(root_real, target_real):
        return identity, "path_escapes_root"
    if excluded_namespace(repo, policy["excluded_github_owners"]) or excluded_namespace(
            root + "/" + path + "/" + target_real, policy["excluded_github_owners"]):
        return identity, "company_private_namespace"
    if policy["credential_paths_denied"] and (credential_path(root) or credential_path(path)
                                              or credential_path(target_real)):
        return identity, "credential_path"
    classification = entry.get("classification")
    if classification is None:
        return identity, "classification_required"
    if classification == "sensitive":
        return identity, "sensitive_content"
    identity["classification"] = classification
    return identity, None


def assess(manifest, input_policy=None):
    """Validate one manifest. Returns {state, refusal, inputs, limits}.

    `state` is `absent` (no manifest), `allowed` (every input allowed and bound), or `refused`
    (at least one router-code reason). A malformed manifest raises ValueError: that is a
    caller error, not a provenance state.
    """
    policy = policy_view(input_policy)
    limits = {"declarations_only": True, "caller_assertion_limit": CALLER_ASSERTION_LIMIT}
    if manifest is None:
        return {"state": "absent", "refusal": "input_provenance_required", "inputs": [], "limits": limits}
    if (not isinstance(manifest, dict) or set(manifest) != MANIFEST_KEYS or manifest.get("v") != 1
            or not isinstance(manifest.get("inputs"), list)):
        raise ValueError("input provenance must be {v: 1, inputs: [...]}")
    if not manifest["inputs"]:
        return {"state": "absent", "refusal": "input_provenance_required", "inputs": [], "limits": limits}
    if len(manifest["inputs"]) > MAX_INPUTS:
        raise ValueError(f"input provenance holds at most {MAX_INPUTS} inputs")
    rows, refusal = [], None
    for number, entry in enumerate(manifest["inputs"]):
        if not isinstance(entry, dict) or entry.get("kind") not in KINDS:
            raise ValueError(f"input provenance inputs[{number}] needs kind github|local")
        kind = entry["kind"]
        allowed_keys = GITHUB_KEYS if kind == "github" else LOCAL_KEYS
        if not set(entry) <= allowed_keys:
            raise ValueError(f"input provenance inputs[{number}] has keys that {kind} inputs do not take")
        if entry.get("visibility") is not None and entry["visibility"] not in VISIBILITIES:
            raise ValueError(f"input provenance inputs[{number}].visibility must be public or private")
        if entry.get("classification") is not None and entry["classification"] not in CLASSIFICATIONS:
            raise ValueError(f"input provenance inputs[{number}].classification must be one of "
                             f"{', '.join(CLASSIFICATIONS)}")
        contains = entry.get("contains")
        if contains is not None and not (isinstance(contains, list) and contains
                                         and set(contains) <= set(PROHIBITED_CONTENT)):
            raise ValueError(f"input provenance inputs[{number}].contains must be drawn from "
                             f"{', '.join(PROHIBITED_CONTENT)}")
        if kind == "github":
            reason = _assess_github(entry, policy)
            identity = {"kind": "github", "repo": canonical_repo(entry["repo"]),
                        "visibility": entry.get("visibility"), "state": "allowed" if reason is None else "refused",
                        "refusal": reason}
        else:
            identity, reason = _assess_local(entry, policy)
            identity["state"] = "allowed" if reason is None else "refused"
            identity["refusal"] = reason
        if contains is not None and reason is None:
            reason = "prohibited_content:" + contains[0]
            identity.update(state="refused", refusal=reason)
        rows.append(identity)
        refusal = refusal or reason
    return {"state": "allowed" if refusal is None else "refused", "refusal": refusal,
            "inputs": rows, "limits": limits}


def parse(raw):
    """Parse one `--input-provenance` value into a manifest. Raises ValueError on malformed JSON."""
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except ValueError as error:
        raise ValueError("--input-provenance must be a JSON object") from error
    if not isinstance(value, dict):
        raise ValueError("--input-provenance must be a JSON object")
    return value


def summary(state):
    """Carry the provenance state into every output, without duplicating the manifest."""
    return {"state": state["state"], "refusal": state["refusal"], "inputs": state["inputs"], "limits": state["limits"]}
