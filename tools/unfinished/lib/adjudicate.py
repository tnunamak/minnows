"""R4: a stronger model (Sonnet-class, different from the extractor) reads each
item with its evidence and writes the ledger fields.

Policy (from design reviews):
- text evidence can make an item "likely-done" (shown collapsed, with the link
  for the owner to confirm), never silently closed;
- "done"/"superseded" need a deterministic signal (merged/closed PR, lane outcome)
  or an explicit statement in a later turn of the same work;
- "stale" needs a stated reason (object gone, superseded, overtaken), not age alone.
Writes table `adj(item_id, out json, model)`.
"""
import datetime as dt, json, os, sqlite3, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from jev import redact
from uf_config import CFG, STATE, WORK_DB, claude_env, owner

MODEL = os.environ.get("ADJ_MODEL") or CFG["adjudicate_model"]
BATCH = int(os.environ.get("ADJ_BATCH", "8"))
TODAY = dt.date.today().isoformat()
O = owner()

SCHEMA = {"type": "object", "properties": {"results": {"type": "array", "items": {"type": "object", "properties": {
    "id": {"type": "string"},
    "verdict": {"type": "string", "enum": ["open", "likely-done", "done", "superseded", "stale", "not-actionable"]},
    "verdict_reason": {"type": "string"},
    "needs_owner": {"type": "boolean"},
    "next_action": {"type": "string"},
    "why": {"type": "string"},
    "priority": {"type": "integer", "minimum": 1, "maximum": 5},
    "effort": {"type": "string", "enum": ["minutes", "hour", "half-day", "days"]},
    "duplicate_of": {"type": "string"},
    "confidence": {"type": "string", "enum": ["low", "medium", "high"]}},
    "required": ["id", "verdict", "verdict_reason", "needs_owner", "next_action", "why", "priority", "effort",
                 "duplicate_of", "confidence"]}}}, "required": ["results"]}

PROMPT = f"""Today is {TODAY}. You are auditing a ledger of {O}'s UNFINISHED BUSINESS: things decided, planned, promised or asked for in their AI-agent work sessions, threads, PRs and notes that may never have been finished. {O} runs many agents in parallel and needs each item to be actionable without re-reading anything.

For each item you get: the extracted item, the source excerpt, later turns from the same session (with a cheap model's probabilities that they picked the item up), GitHub PR states for referenced PRs, and later messages from other sessions that mention the item's identifiers.

Decide for each item:
- verdict:
  - open: still needs doing, and the evidence does not show it done
  - likely-done: later evidence suggests it was done but nothing authoritative confirms it; {O} should glance and tick it off
  - done: an authoritative signal (merged PR that is clearly this work, explicit later report that exactly this was finished) shows it finished
  - superseded: later evidence shows it was replaced, cancelled, or made moot
  - stale: no longer worth doing for a STATED reason (the thing it concerns is gone, overtaken by later work, a one-off moment that passed)
  - not-actionable: not real work (a caveat, a status narration, a standing rule, a recap request, a smoke test)
- verdict_reason: one short sentence naming the evidence ("PR merged 10-06", "later turn says fixed", "no later mention; still blocks X").
- needs_owner: true only if {O} personally must decide, approve, provide, or do it (not an agent).
- next_action: imperative, at most 16 words, concrete (names the repo/PR/file). For decisions, phrase as the question to decide.
- why: at most 18 words: the concrete cost of leaving it undone.
- priority: 5 = blocks shipping, money, security or a teammate now; 4 = important deliverable or decision; 3 = useful; 2 = nice to have; 1 = negligible.
- effort: minutes / hour / half-day / days.
- duplicate_of: if another item IN THIS BATCH is the same piece of work, the id of the better-worded one; else "".
- confidence: low / medium / high in the verdict.

Be skeptical of agent claims that something is finished; prefer authoritative signals. Absence of later mention is not proof of anything; say "no later mention" and keep it open unless there is a stated reason it is stale. Return JSON matching the schema, one result per item id.

ITEMS:
"""


def pack(db, iid):
    it = db.execute("""select item_id, source_type, kind, project, title, detail, owner, ts, last_ts, status_reason, quote,
                       seg_id, links from items where item_id=?""", (iid,)).fetchone()
    (iid, st, kind, project, title, detail, owner, ts, last_ts, sreason, quote, seg_id, links) = it
    d = {"id": iid, "source": st, "kind": kind, "project": project, "item": title, "owner_guess": owner,
         "first_seen": (ts or "")[:10], "last_activity": (last_ts or "")[:10], "status_note": sreason}
    try:
        dj = json.loads(detail or "{}")
        d["detail"] = {k: dj.get(k) for k in ("object", "why", "weight", "trigger")} if isinstance(dj, dict) else detail
    except Exception:
        d["detail"] = (detail or "")[:600]
    if seg_id:
        seg = db.execute("select text, context, kind from segments where seg_id=?", (seg_id,)).fetchone()
        if seg:
            text, ctx, sk = seg
            d["source_excerpt"] = {"before": (ctx or "")[-700:], "text": (text or "")[-1800:] if sk != "user" else (text or "")[:1800]}
        r1 = db.execute("select answers, n_turns from r1 where item_id=?", (iid,)).fetchone()
        if r1 and r1[1]:
            a = json.loads(r1[0])
            rows = db.execute("""select s2.ts, s2.context, s2.text from segments s1 join segments s2
                on s2.session_key=s1.session_key and s2.kind='reply' and s2.ordinal>s1.ordinal
                where s1.seg_id=? order by s2.ordinal limit ?""", (seg_id, r1[1])).fetchall()
            later = []
            for k, (t, c, x) in enumerate(rows):
                w = (a.get(f"work_{k}") or {}).get("noul", 0)
                dn = (a.get(f"done_{k}") or {}).get("noul", 0)
                rr = (a.get(f"reraise_{k}") or {}).get("noul", 0)
                if max(w, dn, rr) >= 0.5:
                    later.append({"date": (t or "")[:10], "p_works_on": round(w, 2), "p_done": round(dn, 2),
                                  "p_owner_reraises": round(rr, 2), "owner": (c or "")[:300], "assistant": (x or "")[:600]})
            d["later_same_session"] = later[:4] or "no later turn in this session deals with it"
    ev = db.execute("select prs, snippets from evidence where item_id=?", (iid,)).fetchone()
    if ev:
        prs, snips = json.loads(ev[0] or "[]"), json.loads(ev[1] or "[]")
        if prs:
            d["github_prs"] = [{k: p.get(k) for k in ("ref", "state", "title", "mergedAt", "closedAt", "isDraft", "error") if p.get(k)} for p in prs]
        if snips:
            d["later_mentions_elsewhere"] = [{k: s[k] for k in ("ts", "role", "same_session", "text")} for s in snips[:5]]
    return d


def call(batch_json):
    prompt = PROMPT + redact(json.dumps(batch_json, ensure_ascii=False, indent=0))
    env = claude_env()
    err = ""
    for attempt in range(3):
        try:
            p = subprocess.run(["claude", "-p", "--model", MODEL, "--tools", "", "--strict-mcp-config",
                                "--disable-slash-commands", "--setting-sources", "", "--no-session-persistence",
                                "--output-format", "json", "--json-schema", json.dumps(SCHEMA)],
                               input=prompt, capture_output=True, text=True, timeout=600, env=env,
                               cwd=str(STATE))
            out = json.loads(p.stdout)
            if out.get("is_error"):
                raise RuntimeError(str(out.get("result"))[:200])
            return out.get("structured_output") or json.loads(out["result"].strip().strip("`").removeprefix("json")), out.get("total_cost_usd", 0)
        except Exception as e:
            err = str(e)
            time.sleep(10 * (attempt + 1))
    return {"_error": err}, 0


def main():
    db = sqlite3.connect(str(WORK_DB), timeout=600)
    db.execute("create table if not exists adj (item_id text primary key, out text, model text)")
    sel = sys.argv[1] if len(sys.argv) > 1 else "select item_id from prerank order by score desc"
    ids = [r[0] for r in db.execute(sel).fetchall()]
    done = {r[0] for r in db.execute("select item_id from adj")}
    ids = [i for i in ids if i not in done]
    # group by project so duplicates land in the same batch
    proj = {r[0]: r[1] or "" for r in db.execute("select item_id, project from items")}
    ids.sort(key=lambda i: proj.get(i, ""))
    batches = [ids[i:i + BATCH] for i in range(0, len(ids), BATCH)]
    print(f"items={len(ids)} batches={len(batches)}", file=sys.stderr)
    packs = [[pack(db, i) for i in b] for b in batches]
    cost, n = 0.0, 0
    with ThreadPoolExecutor(int(os.environ.get("ADJ_WORKERS", "6"))) as ex:
        for b, (res, c) in zip(batches, ex.map(call, packs)):
            n += 1
            cost += c or 0
            if "_error" in res:
                print("error", res["_error"][:200], file=sys.stderr)
                continue
            for r in res.get("results", []):
                if r.get("id") in b:
                    db.execute("insert or replace into adj values (?,?,?)", (r["id"], json.dumps(r), MODEL))
            db.commit()
            if n % 10 == 0:
                print(f"{n}/{len(batches)} cost=${cost:.2f}", file=sys.stderr)
    print(f"done cost=${cost:.2f}", file=sys.stderr)


if __name__ == "__main__":
    main()
