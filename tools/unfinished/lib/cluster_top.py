"""Final duplicate pass over the checked-open items (the part the owner reads first).

One Sonnet call sees every checked-open or unclear item and groups those that
are the same piece of work or the same decision. Output goes to table
`top_clusters(item_id, survivor, merged_action)`; render.py collapses each group
into its survivor and shows the others as "also seen". Reversible: drop the
table to undo.
"""
import json, sqlite3, subprocess, sys
from uf_config import CFG, STATE, WORK_DB, claude_env, owner

SCHEMA = {"type": "object", "properties": {"groups": {"type": "array", "items": {"type": "object", "properties": {
    "ids": {"type": "array", "items": {"type": "string"}},
    "survivor": {"type": "string"},
    "merged_next_action": {"type": "string"}},
    "required": ["ids", "survivor", "merged_next_action"]}}}, "required": ["groups"]}

PROMPT = f"""Below are open items from {owner()}'s ledger of unfinished work. Several are the same piece of work or the same pending decision, extracted from different places. Group ONLY items where doing or deciding one would fully settle the others (same PR, same decision, same physical task). Related-but-different steps stay separate. For each group of 2 or more, choose the survivor (the most specific, best-worded id) and write one merged next_action (imperative, at most 18 words, naming the exact repo/PR/file; for decisions, the question to decide). Return only groups with 2 or more ids.

ITEMS:
"""


def main():
    db = sqlite3.connect(str(WORK_DB), timeout=600)
    rows = db.execute("""select r.item_id, i.project, json_extract(r.out,'$.next_action'), json_extract(r.out,'$.evidence')
                         from resolved r join items i using(item_id)
                         where json_extract(r.out,'$.status_now') in ('open','unclear')""").fetchall()
    payload = [{"id": i, "project": p, "next_action": a, "evidence": (e or "")[:300]} for i, p, a, e in rows]
    env = claude_env()
    p = subprocess.run(["claude", "-p", "--model", CFG["adjudicate_model"], "--tools", "", "--strict-mcp-config",
                        "--disable-slash-commands", "--setting-sources", "", "--no-session-persistence",
                        "--output-format", "json", "--json-schema", json.dumps(SCHEMA)],
                       input=PROMPT + json.dumps(payload, ensure_ascii=False, indent=0), capture_output=True,
                       text=True, timeout=600, env=env, cwd=str(STATE))
    out = json.loads(p.stdout)
    groups = (out.get("structured_output") or {}).get("groups", [])
    ids = {r[0] for r in rows}
    db.execute("drop table if exists top_clusters")
    db.execute("create table top_clusters (item_id text primary key, survivor text, merged_action text)")
    n = 0
    for g in groups:
        members = [i for i in g["ids"] if i in ids]
        surv = g["survivor"] if g["survivor"] in members else (members[0] if members else None)
        if not surv or len(members) < 2:
            continue
        for i in members:
            db.execute("insert or replace into top_clusters values (?,?,?)", (i, surv, g["merged_next_action"]))
        n += 1
    db.commit()
    print(f"items={len(rows)} groups={n} cost=${out.get('total_cost_usd', 0):.2f}", file=sys.stderr)
    for g in groups:
        print(" ", g["survivor"], "<-", [i for i in g["ids"] if i != g["survivor"]], "::", g["merged_next_action"], file=sys.stderr)


if __name__ == "__main__":
    main()
