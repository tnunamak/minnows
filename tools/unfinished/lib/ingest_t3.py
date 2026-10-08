"""Ingest T3 threads: metadata for every thread, last-turn text for recent ones.

Read-only (`t3code threads list/read`). Writes tables `t3threads` and
segments of kind `t3tail`, and maps each thread to a native session key by
matching its final message text against the convo ledger (for dedup).
"""
import json, re, sqlite3, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from uf_config import CFG, CONVO_LEDGER, WORK_DB, since_default

CONVO = str(CONVO_LEDGER)
SINCE = sys.argv[1] if len(sys.argv) > 1 else since_default()


def norm(t):
    return re.sub(r"\s+", " ", t or "").strip()[:200]


def t3url(env_id, tid):
    from urllib.parse import quote
    return f"t3-thread://v1/{env_id}/{quote(tid, safe='')}"


def read(tid):
    r = subprocess.run(["t3code", "--json", "threads", "read", "--thread", tid, "--detail", "answers",
                        "--turns", "2", "--max-chars", "6000"], capture_output=True, text=True, timeout=120)
    try:
        return tid, json.loads(r.stdout)["data"]["thread"]
    except Exception:
        return tid, None


def main():
    if not CFG["t3"]:
        return
    lst = json.loads(subprocess.run(["t3code", "--json", "threads", "list", "--status", "all"],
                                    capture_output=True, text=True, check=True).stdout)["data"]
    env_id = lst["runtime"]["environmentId"]
    projects = {p["id"]: p for p in lst["projects"]}
    db = sqlite3.connect(str(WORK_DB), timeout=600)
    db.execute("""create table if not exists t3threads (thread_id text primary key, title text, status text,
                  settled_at text, settled_override text, auto_settle_disabled text, snoozed_until text, project text,
                  project_root text, provider text, created text, last_user text, prs text, url text, session_key text,
                  creation_source text, parent text)""")
    recent = []
    prev = {r[0]: r[1] for r in db.execute(
        "select t.thread_id, s.ts from t3threads t join segments s on s.seg_id = 't3-' || substr(t.thread_id, -40)")}
    for t in lst["threads"]:
        if t.get("deletedAt"):
            continue
        p = projects.get(t["projectId"], {})
        last = t.get("latestUserMessageAt") or t.get("latestRunCompletedAt") or t.get("createdAt") or ""
        prs = [{"url": x["url"], "state": (x.get("snapshot") or {}).get("state"),
                "title": (x.get("snapshot") or {}).get("title")} for x in (t.get("pullRequests") or [])]
        db.execute("insert or replace into t3threads values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (t["id"], t["title"], t["status"], t.get("settledAt"), json.dumps(t.get("settledOverride")),
                    t.get("autoSettleDisabledAt"), t.get("snoozedUntil"), p.get("title"), p.get("workspaceRoot"),
                    t.get("providerInstanceId"), t.get("createdAt"), last, json.dumps(prs), t3url(env_id, t["id"]), None,
                    t.get("creationSource"), (t.get("lineage") or {}).get("parentThreadId")))
        # re-read only threads with activity after their stored last turn (or never read)
        # updatedAt also moves on settle/visit, so compare the last run's completion instead
        activity = max(t.get("latestRunCompletedAt") or "", t.get("latestUserMessageAt") or "")
        if last >= SINCE and (t["id"] not in prev or activity > (prev[t["id"]] or "")):
            recent.append(t["id"])
    db.commit()
    print(f"threads: {len(lst['threads'])}; recent: {len(recent)}", file=sys.stderr)
    src = sqlite3.connect(f"file:{CONVO}?mode=ro", uri=True)
    idx = {}
    for harn, sess, path, text in src.execute("""select s.harness, s.session_id, s.path, m.text from messages m
            join source_files s on s.id=m.source_id where m.message_ts>=? and m.role='assistant'""", (SINCE,)):
        idx.setdefault(norm(text), f"{harn}:{sess or path}")
    mapped = 0
    with ThreadPoolExecutor(8) as ex:
        for tid, th in ex.map(read, recent):
            if not th:
                continue
            msgs = th.get("messages") or []
            finals = [m for m in msgs if m.get("role") == "assistant" and m.get("text")]
            users = [m for m in msgs if m.get("role") == "user" and m.get("text")]
            if not finals:
                continue
            f = finals[-1]
            key = idx.get(norm(f["text"]))
            if key:
                mapped += 1
                db.execute("update t3threads set session_key=? where thread_id=?", (key, tid))
            db.execute("""insert or replace into segments (seg_id, kind, harness, session_key, source_path, project,
                          ts, ordinal, text, context, unanswered, human_rule, next_ts)
                          values (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       ("t3-" + tid[-40:], "t3tail", "t3", key or ("t3:" + tid), t3url(env_id, tid), th.get("worktreePath"),
                        f.get("createdAt") or f.get("updatedAt"), 0, f["text"][:6000],
                        (users[-1]["text"][:1500] if users else ""), 0, 0, None))
            db.commit()
    db.commit()
    print(f"mapped to native sessions: {mapped}", file=sys.stderr)


if __name__ == "__main__":
    main()
