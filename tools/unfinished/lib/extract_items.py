"""Pass 2: turn candidate segments into typed unfinished-business items with a
cheap Claude model (headless `claude -p`, no tools).

Input: segments chosen by select.py (table `cands`). Output: table `items`
rows with source_type='session' and table `extract_log` (one row per batch, so
reruns skip finished batches).
"""
import hashlib, json, os, sqlite3, subprocess, sys, time
from jev import redact
from concurrent.futures import ThreadPoolExecutor
from uf_config import CFG, STATE, WORK_DB, claude_env, owner

MODEL = os.environ.get("EXTRACT_MODEL") or CFG["extract_model"]
BATCH = 6

SCHEMA = {
    "type": "object",
    "properties": {"results": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "seg": {"type": "string"},
            "items": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "kind": {"type": "string", "enum": ["decision-needed", "approval-needed", "deferred-plan",
                                                         "agent-promise", "follow-up", "blocked", "interrupted",
                                                         "unapplied-decision", "question-unanswered", "standing-rule", "recap-request"]},
                    "owner": {"type": "string", "enum": ["owner", "agent", "either"]},
                    "object": {"type": "string"},
                    "repo": {"type": "string"},
                    "quote": {"type": "string"},
                    "why": {"type": "string"},
                    "weight": {"type": "string", "enum": ["trivial", "minor", "real", "major"]},
                },
                "required": ["action", "kind", "owner", "object", "repo", "quote", "why", "weight"]}}},
        "required": ["seg", "items"]}}},
    "required": ["results"]}

O = owner()
PROMPT = f"""You find UNFINISHED BUSINESS in excerpts of AI-agent work sessions. The person who runs the agents is called {O} below.

Each excerpt is a point where the conversation paused ({O} did not reply for hours, or the session ended), or a message where {O} stated a plan or decision. For each excerpt, list the concrete pieces of work or decisions that were still OPEN at that moment and that {O} would want to be reminded of if they were later forgotten.

Include:
- decisions or approvals the agent asked {O} for (decision-needed / approval-needed)
- work {O} deferred or planned ("hold off until...", "next we'll...", "later") (deferred-plan)
- things the agent promised to do later, or listed as remaining / follow-ups / next steps (agent-promise / follow-up)
- work that was blocked on something (blocked), or cut off by a usage limit, crash or error (interrupted)
- a decision {O} made that the reply did not yet apply (unapplied-decision)
- a question {O} asked that the reply did not answer (question-unanswered)

Exclude:
- narration of steps the agent was doing right then ("checking CI now", "let me read the file")
- work the excerpt itself reports as finished
- generic advice, explanations, or options {O} was not asked to act on
- test/smoke sessions with no real work
- caveat lists in a worker's final report to another agent ("tests not run", "CI pending") unless the report asks {O} for something
- standing instructions that apply to all work ("always delegate", "clean up after yourself"): emit them with kind standing-rule
- requests for a recap ("remind me where we are"): emit them with kind recap-request
- relayed messages from other agents pasted into the chat are context, not {O}'s own words

When the excerpt is {O} re-raising something ("what about X?", "you forgot Y"), the item is X or Y itself, stated so it can be acted on.

Rules:
- `action`: an imperative next action, at most 16 words, specific enough to act on without the transcript (name the PR, repo, file, feature).
- `object`: the concrete thing (e.g. "api PR #346", "billing retry lock", "release checklist").
- `repo`: the repo or project name if known, else "".
- `quote`: a verbatim span of at most 30 words copied from the excerpt that shows the item is open.
- `why`: at most 18 words: what goes wrong or is lost if it stays undone.
- `weight`: trivial (chores, reminders with no consequence), minor, real (a real deliverable or decision), major (blocks shipping, money, security, or a teammate).
- At most 4 items per excerpt, most important first. Return an empty list when nothing is open. Precision matters more than recall.

Return JSON matching the schema, one result per excerpt, using the excerpt's seg id.

EXCERPTS:
"""


def packet(r):
    seg_id, kind, harness, project, ts, text, context, unanswered, trig = r
    if kind == "user":
        body = f"[assistant said before]\n{(context or '')[-1200:]}\n\n[TIM]\n{text[:3000]}"
    else:
        body = f"[TIM's last message]\n{(context or '')[:1200]}\n\n[ASSISTANT reply at pause]\n{text[:4500]}"
        if unanswered:
            body += "\n\n[note: the session ended with a user message left unanswered]"
    return f"=== seg {seg_id} | {kind} | trigger {trig} | {harness} | project {project} | {ts} ===\n{redact(body)}\n"


def call(batch):
    prompt = PROMPT + "\n".join(packet(r) for r in batch)
    env = claude_env()
    for attempt in range(3):
        try:
            p = subprocess.run(["claude", "-p", "--model", MODEL, "--tools", "", "--strict-mcp-config",
                                "--disable-slash-commands", "--setting-sources", "", "--no-session-persistence",
                                "--output-format", "json", "--json-schema", json.dumps(SCHEMA)],
                               input=prompt, capture_output=True, text=True, timeout=400, env=env,
                               cwd=str(STATE))
            out = json.loads(p.stdout)
            if out.get("is_error"):
                raise RuntimeError(str(out.get("result"))[:200])
            res = out.get("structured_output") or json.loads(out["result"].strip().strip("`").removeprefix("json"))
            return res, out.get("total_cost_usd", 0)
        except Exception as e:
            err = str(e)
            time.sleep(5 * (attempt + 1))
    return {"_error": err}, 0


def main():
    db = sqlite3.connect(str(WORK_DB), timeout=600)
    db.execute("create table if not exists extract_log (batch_id text primary key, segs text, out text, cost real)")
    db.execute("create table if not exists seg_items (seg_id text primary key, items text, model text)")
    rows = db.execute("""select s.seg_id, s.kind, s.harness, s.project, s.ts, s.text, s.context, s.unanswered, c.why
                         from cands c join segments s using(seg_id)
                         where s.seg_id not in (select seg_id from seg_items)
                         order by s.session_key, s.ts limit ?""", (int(os.environ.get("EXTRACT_LIMIT", "1000000")),)).fetchall()
    if os.environ.get("EXTRACT_REVERSE"):
        rows = rows[::-1]
    batches = [rows[i:i + BATCH] for i in range(0, len(rows), BATCH)]
    print(f"todo segs={len(rows)} batches={len(batches)}", file=sys.stderr)
    cost = 0.0
    n = 0
    workers = int(os.environ.get("EXTRACT_WORKERS", "6"))
    with ThreadPoolExecutor(workers) as ex:
        for b, (res, c) in zip(batches, ex.map(call, batches)):
            cost += c or 0
            n += 1
            bid = hashlib.sha256((MODEL + "|".join(r[0] for r in b)).encode()).hexdigest()[:16]
            db.execute("insert or replace into extract_log values (?,?,?,?)",
                       (bid, json.dumps([r[0] for r in b]), json.dumps(res), c))
            if "_error" not in res:
                want = {r[0] for r in b}
                got = set()
                for r in res.get("results", []):
                    if r.get("seg") in want:
                        got.add(r["seg"])
                        db.execute("insert or replace into seg_items values (?,?,?)",
                                   (r["seg"], json.dumps(r.get("items", [])), MODEL))
                for sgid in want - got:  # the model omits segments with nothing open
                    db.execute("insert or ignore into seg_items values (?,?,?)", (sgid, "[]", MODEL + ":omitted"))
            db.commit()
            if n % 20 == 0:
                print(f"{n}/{len(batches)} cost=${cost:.2f}", file=sys.stderr)
    print(f"done cost=${cost:.2f}", file=sys.stderr)


if __name__ == "__main__":
    main()
