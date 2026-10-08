"""Cross-batch duplicate collapse for display. Reversible: a duplicate keeps its
own row in ledger.jsonl and is listed under its survivor's "also seen".

Candidate pairs: same project, content-word Jaccard >= 0.25 on next_action +
object. Jev decides "same piece of work" per pair; only p >= 0.85 collapses,
and only into a survivor that is itself not a duplicate (no chains, no union-find).
"""
import re, sqlite3, sys
import jev
from uf_config import WORK_DB

STOP = set("the a an and or to of for in on with is it this that be by as at from not now then into fix add run make check decide".split())


def words(s):
    return {w for w in re.findall(r"[a-z0-9#][a-z0-9#._-]{2,}", (s or "").lower()) if w not in STOP}


def main():
    db = sqlite3.connect(str(WORK_DB), timeout=600)
    db.execute("create table if not exists dups (item_id text primary key, survivor text, p real)")
    rows = db.execute("""select a.item_id, i.project, json_extract(a.out,'$.next_action'), case when json_valid(i.detail) then json_extract(i.detail,'$.object') end,
                         json_extract(a.out,'$.priority'), json_extract(a.out,'$.verdict'), i.ts
                         from adj a join items i using(item_id)
                         where json_extract(a.out,'$.verdict') in ('open','likely-done')""").fetchall()
    byp = {}
    for r in rows:
        byp.setdefault((r[1] or "").lower(), []).append(r)
    pairs = []
    for p, xs in byp.items():
        for i in range(len(xs)):
            for j in range(i + 1, len(xs)):
                a, b = words((xs[i][2] or "") + " " + (xs[i][3] or "")), words((xs[j][2] or "") + " " + (xs[j][3] or ""))
                if a and b and len(a & b) / len(a | b) >= 0.25:
                    pairs.append((xs[i], xs[j]))
    print(f"candidate pairs: {len(pairs)}", file=sys.stderr)
    items = [(f"{x[0]}|{y[0]}", {"item_a": x[2], "item_b": y[2]}) for x, y in pairs]
    res = jev.ask_many(items, lambda i, s: {"same": {"type": "noul", "instructions":
        "Are `item_a` and `item_b` the same piece of work, so that finishing one finishes the other?",
        "criteria": {"true": "Same task on the same thing, just worded differently.",
                     "false": "Different tasks, different steps, or only related."}}}, workers=32)
    meta = {r[0]: r for r in rows}
    db.execute("delete from dups")
    taken = {}
    for key, a in sorted(res.items(), key=lambda kv: -((kv[1].get("same") or {}).get("noul", 0))):
        pr = (a.get("same") or {}).get("noul", 0)
        if pr < 0.85:
            continue
        x, y = key.split("|")
        # survivor: higher priority, then earlier first-seen
        sx, sy = meta[x], meta[y]
        px, py = sx[4] or 0, sy[4] or 0
        x_first = px > py if px != py else (sx[6] or "~") <= (sy[6] or "~")
        surv, dup = (x, y) if x_first else (y, x)
        if dup in taken or surv in taken:
            continue
        taken[dup] = surv
        db.execute("insert or replace into dups values (?,?,?)", (dup, surv, pr))
    db.commit()
    print(f"collapsed: {len(taken)} usage={jev.usage}", file=sys.stderr)


if __name__ == "__main__":
    main()
