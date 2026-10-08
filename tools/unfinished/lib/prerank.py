"""Cheap pre-ranking in code: decides adjudication order and which items become
hygiene bundles instead of rows. Weights are explicit and editable here.
"""
import json, sqlite3, sys
from uf_config import WORK_DB, since_default

WEIGHT = {"major": 3, "real": 2, "minor": 1, "trivial": 0}
TRIGGER = {"tim-reraise": 2, "before-reraise": 1.5, "tim-deferral": 1.5, "asks-tim": 1.5, "tim-unanswered": 1,
           "cut-off": 1, "t3-open": 1, "day-end": 0.5, "worker-asks": 0.3}
DROP_KINDS = {"standing-rule", "recap-request"}


def mx(a, prefix, n):
    return max([(a.get(f"{prefix}_{k}") or {}).get("noul", 0) for k in range(n)] or [0])


def main():
    db = sqlite3.connect(str(WORK_DB), timeout=600)
    db.execute("drop table if exists prerank")
    db.execute("create table prerank (item_id text primary key, score real, bundle text, picked real, done real, reraised real)")
    for iid, st, kind, owner, detail, sreason, title, last_ts in db.execute(
            "select item_id, source_type, kind, owner, detail, status_reason, title, last_ts from items").fetchall():
        bundle, picked, done, rer = None, 0, 0, 0
        if st == "session":
            if kind in DROP_KINDS:
                continue
            d = json.loads(detail or "{}")
            s = WEIGHT.get(d.get("weight"), 1) + TRIGGER.get(d.get("trigger"), 0) + (1 if owner == "owner" else 0)
            r = db.execute("select answers, n_turns from r1 where item_id=?", (iid,)).fetchone()
            if r and r[1]:
                a = json.loads(r[0])
                picked, done, rer = mx(a, "work", r[1]), mx(a, "done", r[1]), mx(a, "reraise", r[1])
                s += (-1 if picked >= 0.7 else 0) + (-2 if done >= 0.8 else 0) + (1.5 if rer >= 0.7 else 0)
        elif st == "pr":
            if kind == "review-request":
                s = 2 if (last_ts or "") >= since_default(100) else 0.2
                bundle = None if s >= 1 else "old-review-requests"
            else:
                det = json.loads(detail or "{}")
                age = det.get("days_since_update") or 0
                if age > 30:
                    s, bundle = 0.5, ("dead-prs-old" if (last_ts or "") < since_default(270) else "abandoned-prs")
                else:
                    s = 2.5 if not det.get("draft") else 1.5
                    s += 0.5 if (det.get("checks_failed") or 0) > 0 else 0
        elif st == "inbox":
            s = 2.5 if "must-fix" in (sreason or "") else 1.5
        elif st == "miss":
            s = 2 if "open" in (sreason or "") else 1
        elif st == "review":
            s = 3.5
        elif st == "lane":
            s, bundle = 0.8, "unclosed-lanes"
        elif st == "worktree":
            s = 1.5 if (last_ts or "") >= since_default() else 1
        else:
            s = 1
        db.execute("insert into prerank values (?,?,?,?,?,?)", (iid, s, bundle, picked, done, rer))
    db.commit()
    for r in db.execute("""select i.source_type, count(*), sum(p.bundle is not null), round(avg(score),2)
                           from prerank p join items i using(item_id) group by 1"""):
        print(*r, file=sys.stderr)


if __name__ == "__main__":
    main()
