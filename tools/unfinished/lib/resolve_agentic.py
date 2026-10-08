"""R5: check the current state of top-ranked items with a tool-using agent.

A hand-checked sample showed that text evidence cannot tell open from done: about 70%
of items that looked open had been finished, replaced, or answered in another
session. So the highest-ranked unchecked items go to a headless Claude (Sonnet)
with READ-ONLY tools (gh read commands, git log/show/branch, convo, t3code read,
file reads). It writes status_now + evidence and rewrites open items.

  resolve_agentic.py [N]     check the top N unchecked open items (default 24)
"""
import datetime as dt, json, os, sqlite3, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from uf_config import CFG, DATA, STATE, WORK_DB, claude_env, owner

OUT = str(DATA)
BRIEF_OVERRIDE = STATE / "resolve-brief.md"  # optional owner-specific brief in the data folder


def default_brief():
    O = owner()
    roots = ", ".join(CFG["repo_roots"])
    return f"""You are the resolver for {O}'s ledger of unfinished business. Each item was extracted from agent sessions, chat threads, PRs or notes. Cheap models flagged it as open, but most such items turn out to be already done, superseded, or answered in another session. Find each item's CURRENT state (today is {dt.date.today().isoformat()}) and, if it is still open, rewrite it so {O} can act on it without reading anything else.

Strictly read-only: change no repo, session, thread or issue. No git fetch/push/checkout/commit. Never print secrets. Never load whole transcripts.

Tools: `convo search '<phrase>'` and `convo grep '<phrase>' --all-projects --since 45d | head -40` search all past sessions; `convo show <session-id> --all-projects -n 6 | head -150` shows a session's last exchanges. `gh pr view`, `gh pr list -R OWNER/REPO --search '<terms>' --state all` and `gh issue view` read GitHub. `cd <repo> && git log --since=<date> --oneline | head`, `cd <repo> && git log --all -S '<identifier>' --oneline | head` and `cd <repo> && git branch -a --list '*<name>*'` read local history; repos live under {roots}. Local clones may be stale, so prefer `gh` for remote state.

Common traps: the answer or decision happened in a different session (often a parent or orchestrating session); the work merged minutes to days later; a bare PR number belongs to a different repo than the item names; an outage was fixed but a durable follow-up was not; {O} answered some decisions in a list and not others.

For each item: read its evidence_page, then investigate what happened after first_seen (at most about 7 tool calls). Decide:
- status_now: open / done / superseded / stale / unclear
- evidence: one line naming what you checked and found (session id and date, PR URL and state, commit sha, file).
- If open or unclear: next_action (imperative, at most 16 words, naming the exact repo/PR/branch/file; for a decision, the question to decide), why (at most 18 words: the cost of leaving it undone), needs_owner (true only if {O} personally must decide, approve, provide or do it), priority (5 blocks shipping, money, security or a teammate now; 4 important; 3 useful; 2 nice; 1 negligible), effort (minutes / hour / half-day / days).
If you cannot tell, use unclear; do not guess. Return the results as JSON."""


BATCH = 6
ALLOWED = ["Read", "Grep", "Glob",
           "Bash(gh pr view:*)", "Bash(gh pr list:*)", "Bash(gh issue view:*)", "Bash(cd:*)", "Bash(git log:*)", "Bash(git show:*)", "Bash(git branch -a:*)", "Bash(git branch --list:*)",
           "Bash(convo search:*)", "Bash(convo grep:*)", "Bash(convo show:*)", "Bash(convo list:*)",
           "Bash(t3code threads read:*)", "Bash(t3code --json threads read:*)", "Bash(head:*)"]
DENIED = ["Edit", "Write", "NotebookEdit", "Bash(git push:*)", "Bash(git checkout:*)", "Bash(git commit:*)",
          "Bash(git reset:*)", "Bash(git fetch:*)", "Bash(gh pr merge:*)", "Bash(gh pr comment:*)", "Bash(gh pr close:*)",
          "Bash(gh api -X:*)", "Bash(gh api --method:*)", "Bash(t3code threads send:*)", "Bash(rm:*)"]
SCHEMA = {"type": "object", "properties": {"results": {"type": "array", "items": {"type": "object", "properties": {
    "id": {"type": "string"}, "status_now": {"type": "string", "enum": ["open", "done", "superseded", "stale", "unclear"]},
    "evidence": {"type": "string"}, "next_action": {"type": "string"}, "why": {"type": "string"},
    "needs_owner": {"type": "boolean"}, "priority": {"type": "integer"}, "effort": {"type": "string"}},
    "required": ["id", "status_now", "evidence"]}}}, "required": ["results"]}


def call(batch):
    brief = BRIEF_OVERRIDE.read_text() if BRIEF_OVERRIDE.exists() else default_brief()
    prompt = (brief.replace("Write one JSON object per line to your output file", "Return the results as JSON")
              + "\n\nITEMS:\n" + json.dumps(batch, ensure_ascii=False, indent=1))
    env = claude_env()
    for attempt in range(2):
        try:
            p = subprocess.run(["claude", "-p", "--model", CFG["resolve_model"], "--strict-mcp-config",
                                "--disable-slash-commands", "--setting-sources", "", "--no-session-persistence",
                                "--allowedTools", *ALLOWED, "--disallowedTools", *DENIED,
                                "--output-format", "json", "--json-schema", json.dumps(SCHEMA)],
                               input=prompt, capture_output=True, text=True, timeout=1800, env=env,
                               cwd=str(STATE))
            out = json.loads(p.stdout)
            if out.get("is_error"):
                raise RuntimeError(str(out.get("result"))[:200])
            return out.get("structured_output") or {}, out.get("total_cost_usd", 0)
        except Exception as e:
            err = str(e)
            time.sleep(15)
    return {"_error": err}, 0


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    db = sqlite3.connect(str(WORK_DB), timeout=600)
    db.execute("create table if not exists resolved (item_id text primary key, out text, src text)")
    xs = [json.loads(l) for l in open(os.path.join(OUT, "ledger.jsonl"))]
    done = {r[0] for r in db.execute("select item_id from resolved")}
    pick = [x for x in xs if x.get("verdict") == "open" and x["id"] not in done][:n]
    batch_in = [{"id": x["id"], "project": x["project"], "source_type": x["source_type"],
                 "next_action": x.get("next_action"), "why": x.get("why"), "first_seen": x["first_seen"],
                 "session_key": x.get("session_key"), "prs": [p.get("ref") for p in x.get("prs", [])],
                 "quote": x.get("quote"), "evidence_page": os.path.join(OUT, "evidence", x["slug"] + ".md")} for x in pick]
    batches = [batch_in[i:i + BATCH] for i in range(0, len(batch_in), BATCH)]
    print(f"checking {len(batch_in)} items in {len(batches)} batches", file=sys.stderr)
    cost = 0
    with ThreadPoolExecutor(int(os.environ.get("RESOLVE_WORKERS", "3"))) as ex:
        for b, (res, c) in zip(batches, ex.map(call, batches)):
            cost += c or 0
            if "_error" in res:
                print("error", res["_error"], file=sys.stderr)
                continue
            ids = {x["id"] for x in b}
            for r in res.get("results", []):
                if r.get("id") in ids:
                    db.execute("insert or replace into resolved values (?,?,?)", (r["id"], json.dumps(r), "resolve_agentic"))
            db.commit()
    print(f"done cost=${cost:.2f}", file=sys.stderr)


if __name__ == "__main__":
    main()
