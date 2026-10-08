"""R1: did the same session pick the item up afterwards?

For each extracted session item, take the next 10 exchanges of the same session
and ask Jev, per exchange, whether it works on / finishes / cancels the item, and
whether the owner raises it again. This is local, chronological evidence with small
state per question (Jev's good shape). It never closes an item: a pick-up only
lowers priority; "finished" is a hint the adjudicator must confirm.
"""
import json, sqlite3, sys
import jev
from uf_config import WORK_DB, owner

K = 10


def turn_text(u, r):
    a = r or ""
    a = a if len(a) <= 1100 else a[:700] + " [...] " + a[-400:]
    return {"owner": (u or "")[:700], "assistant": a}


def main():
    db = sqlite3.connect(str(WORK_DB), timeout=600)
    db.execute("create table if not exists r1 (item_id text primary key, answers text, n_turns int)")
    items = db.execute("""select i.item_id, i.title, i.detail, i.quote, i.session_key, s.ordinal, s.ts
                          from items i join segments s on s.seg_id=i.seg_id
                          where i.source_type='session' and i.item_id not in (select item_id from r1)""").fetchall()
    print(f"items to check: {len(items)}", file=sys.stderr)
    packs = []
    for item_id, title, detail, quote, sk, ordinal, ts in items:
        rows = db.execute("""select kind, ordinal, ts, text, context from segments where session_key=? and kind in ('user','reply')
                             and ordinal>? order by ordinal limit ?""", (sk, ordinal, 2 * K)).fetchall()
        turns = []
        for kind, o, t, text, ctx in rows:
            if kind == "reply":
                turns.append(dict(turn_text(ctx, text), date=(t or "")[:10]))
        turns = turns[:K]
        if not turns:
            db.execute("insert or replace into r1 values (?,?,?)", (item_id, json.dumps({}), 0))
            continue
        state = {"item": {"next_action": title, "context": (detail or "")[:300], "quote": quote or ""},
                 "later_turns": turns}
        packs.append((item_id, state, len(turns)))
    db.commit()

    def qs(item_id, state):
        O = owner()
        q = {}
        for k in range(len(state["later_turns"])):
            q[f"work_{k}"] = {"type": "noul", "instructions":
                f"Does `later_turns[{k}]` work on, finish, decide, or explicitly cancel the specific task in `item`?",
                "criteria": {"true": "That turn clearly deals with this same task.",
                             "false": "That turn is about other things, or only mentions it in passing."}}
            q[f"done_{k}"] = {"type": "noul", "instructions":
                f"Does `later_turns[{k}]` report that the specific task in `item` is now finished?"}
            q[f"reraise_{k}"] = {"type": "noul", "instructions":
                f"In `later_turns[{k}].owner`, does {O} bring up the task in `item` again, for example asking about it or saying it was dropped?"}
        return q

    n = {i: c for i, _, c in packs}
    res = jev.ask_many([(i, s) for i, s, _ in packs], qs, workers=32)
    for i, a in res.items():
        if "_error" not in a:
            db.execute("insert or replace into r1 values (?,?,?)", (i, json.dumps(a), n[i]))
    db.commit()
    print(f"usage={jev.usage}", file=sys.stderr)


if __name__ == "__main__":
    main()
