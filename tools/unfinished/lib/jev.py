"""Minimal Jev (TypeSafe System One) client with a disk cache and a thread pool.

The key comes from $TYPESAFE_API_KEY or the configured secret command, and is
never printed.
Cache: sqlite keyed by sha256(state + questions), so reruns are free and idempotent.
"""
import hashlib, json, re, sqlite3, threading, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

from uf_config import JEV_CACHE, secret

CACHE = str(JEV_CACHE)
URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.13.0"  # pinned: thresholds are tuned against this version

_key = None
_lock = threading.Lock()
_local = threading.local()
usage = {"input_tokens": 0, "calls": 0, "cached": 0, "errors": 0}


def _get_key():
    global _key
    if _key is None:
        _key = secret("TYPESAFE_API_KEY")
    return _key


def _db():
    if not hasattr(_local, "db"):
        _local.db = sqlite3.connect(CACHE, timeout=60)
        _local.db.execute("create table if not exists c (k text primary key, v text)")
    return _local.db


SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"),
    re.compile(r"(?i)\b(?:bearer|token|api[_-]?key|secret|password|passwd)(?:\s*[:=]\s*|\s+)['\"]?[A-Za-z0-9_./+=-]{12,}"),
    re.compile(r"(?i)(?<=://)[^\s:/@]+:[^\s@/]+@"),
    re.compile(r"\b[A-Fa-f0-9]{48,}\b"),  # long hex blobs; 40-hex git SHAs are kept
    re.compile(r"\b[A-Za-z0-9+/]{60,}={0,2}"),  # long base64 runs
]


QUERY_SECRET = re.compile(r"(?i)([?&](?:code|state|token|access_token|refresh_token|key|sig|signature|secret)=)[^&\s\"']{8,}")


def redact(obj):
    """Mask likely secrets before any hosted call."""
    if isinstance(obj, str):
        obj = QUERY_SECRET.sub(r"\1[REDACTED]", obj)
        for pat in SECRET_PATTERNS:
            obj = pat.sub("[REDACTED]", obj)
        return obj
    if isinstance(obj, dict):
        return {k: redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def ask(state, questions):
    """Return {qid: answer} for one state. Cached."""
    body = {"state": state, "model": MODEL, "questions": questions}
    k = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    row = _db().execute("select v from c where k=?", (k,)).fetchone()
    if row:
        with _lock:
            usage["cached"] += 1
        return json.loads(row[0])
    # cache key stays on the raw body (stable ids); only the redacted state leaves the machine
    data = json.dumps(dict(body, state=redact(state))).encode()
    for attempt in range(8):
        req = urllib.request.Request(URL, data=data, headers={
            "Authorization": f"Bearer {_get_key()}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                resp = json.loads(r.read())
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 529) and attempt < 7:
                time.sleep(min(30, 1.5 * 2 ** attempt))
                continue
            with _lock:
                usage["errors"] += 1
            raise RuntimeError(f"jev http {e.code}: {e.read()[:300]!r}")
        except (urllib.error.URLError, TimeoutError):
            if attempt < 7:
                time.sleep(min(30, 1.5 * 2 ** attempt))
                continue
            raise
    ans = resp["answers"]
    with _lock:
        usage["input_tokens"] += resp.get("usage", {}).get("input_tokens", 0)
        usage["calls"] += 1
    db = _db()
    db.execute("insert or replace into c values (?,?)", (k, json.dumps(ans)))
    db.commit()
    return ans


def ask_many(items, questions_for, workers=24):
    """items: list of (id, state). questions_for(id, state) -> questions dict.
    Returns {id: answers or {'_error': str}}."""
    out = {}

    def one(it):
        i, st = it
        try:
            return i, ask(st, questions_for(i, st))
        except Exception as e:  # keep going; report errors
            return i, {"_error": str(e)[:300]}

    with ThreadPoolExecutor(workers) as ex:
        for i, a in ex.map(one, items):
            out[i] = a
    return out


def p(ans, qid):
    """Probability-ish scalar for a noul, or the choice label for a choice."""
    a = ans.get(qid) or {}
    if a.get("type") == "noul":
        return a["noul"]
    if a.get("type") == "choice":
        return a["choice"]
    if a.get("type") == "score":
        return a.get("score")
    return None
