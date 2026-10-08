"""Policy v2: choose which segments go to extraction. Thresholds live here, not
in the Jev questions, so they can be retuned without re-scoring.

Why these rules (from two independent design reviews and a hand-checked sample):
- worker sessions (<3 human turns) are evidence, not sources, unless they ask the
  owner something or were cut off;
- triggers are steering events, not silence: replies that ask the owner something,
  the owner's deferrals and decisions, re-raises (and the reply before them), the last
  reply of each session-day, cut-off tails, and T3 last turns;
- saturated nouls (open_work, work_intent) are never used alone.
"""
import os, re, sqlite3, sys
from uf_config import CFG, WORK_DB, since_default


RERAISE = re.compile(r"(?i)\b(what about|remind me|did we ever|we never|you forgot|forgot|forgotten|still not|still isn'?t|"
                     r"back to the|you didn'?t|we didn'?t|dropped|lost track|where are we on|what happened (to|with)|"
                     r"haven'?t (heard|seen)|any update on|i (asked|told) you)\b")
# Agent text relayed into the owner's turn. Owner-specific prefixes (lane or role
# names) go in config.json "relayed_prefixes".
RELAYED = re.compile(r"^(\s*(note from|from the|escalation|version \d+|status:|report:|\[|#|\||>"
                     + "".join("|" + p for p in CFG["relayed_prefixes"]) + "))", re.I)


def relayed(text):
    t = text.strip()
    return bool(RELAYED.match(t)) or (len(t) > 900 and t.count("|") > 12) or t.count("\n- ") > 12


def main():
    db = sqlite3.connect(str(WORK_DB), timeout=600)
    db.create_function("relayed", 1, lambda t: int(relayed(t or "")))
    db.create_function("reraise", 1, lambda t: int(bool(RERAISE.search(t or ""))))
    db.executescript("""
    drop table if exists j;
    create temp table j as
      select s.seg_id, s.kind, s.session_key, s.ts, s.next_ts, s.ordinal, s.unanswered, s.text, coalesce(se.n_human,0) n_human,
        json_extract(a.answers,'$.human.noul') human, json_extract(a.answers,'$.work_intent.noul') intent,
        json_extract(a.answers,'$.deferral.noul') defer, json_extract(a.answers,'$.decision.noul') decision,
        json_extract(a.answers,'$.open_work.noul') open_work, json_extract(a.answers,'$.asks_human.noul') asks,
        json_extract(a.answers,'$.promise.noul') promise, json_extract(a.answers,'$.complete.noul') complete,
        json_extract(a.answers,'$.interrupted.noul') interrupted
      from segments s join jev1 a using(seg_id) left join sessions se on se.session_key = s.session_key;
    create index temp.j_sk on j(session_key, ordinal);
    drop table if exists cands;
    create table cands (seg_id text primary key, why text);
    """)
    tim = "kind='user' and n_human>=3 and human>=0.7 and relayed(text)=0"
    rules = [
        ("tim-deferral", f"{tim} and defer>=0.8"),
        ("tim-reraise", f"{tim} and reraise(text)=1"),
        ("tim-unanswered", f"{tim} and unanswered=1 and intent>=0.7"),
        # asks at a pause (no human reply for 6 h, or session end) or at a tail
        ("asks-tim", "kind in ('reply','tail') and n_human>=3 and asks>=0.5 and (kind='tail' or next_ts is null "
                     "or (julianday(next_ts)-julianday(ts))*24>=6)"),
        ("worker-asks", "kind in ('reply','tail') and n_human<3 and asks>=0.8 and complete<0.3"),
        ("cut-off", "kind='tail' and n_human>=3 and (interrupted>=0.7 or unanswered=1)"),
        ("t3-open", "kind='t3tail' and (asks>=0.5 or interrupted>=0.7 or (promise>=0.8 and complete<0.3))"),
    ]
    for why, cond in rules:
        db.execute(f"insert or ignore into cands select seg_id, '{why}' from j where {cond}")
    # the reply right before a re-raise: the dropped item is usually named there
    db.execute("""insert or ignore into cands
        select r.seg_id, 'before-reraise' from j u join j r on r.session_key=u.session_key and r.kind='reply'
        and r.ordinal = (select max(ordinal) from j r2 where r2.session_key=u.session_key and r2.kind='reply' and r2.ordinal<u.ordinal)
        where u.kind='user' and u.n_human>=3 and u.human>=0.7 and reraise(u.text)=1""")
    # last reply of each session-day that still reports open work it promised
    db.execute("""insert or ignore into cands
        select seg_id, 'day-end' from (
          select seg_id, promise, open_work, complete, n_human, kind,
                 row_number() over (partition by session_key, substr(ts,1,10) order by ordinal desc) rn
          from j where kind='reply') where rn=1 and n_human>=3 and complete<0.4 and (promise>=0.8 or open_work>=0.9)""")
    # mid-session asks: only strong asks whose next owner message does not clearly answer them, or that pose 3+
    # questions (partial answers are the common failure). ASKS_SINCE bounds the volume (default: last 7 days).
    since = os.environ.get("ASKS_SINCE") or since_default(7)
    by_sess = {}
    for r in db.execute("select seg_id, session_key, kind, ts, text, asks, decision, n_human from j order by session_key, ordinal"):
        by_sess.setdefault(r[1], []).append(r)
    keep = []
    for xs in by_sess.values():
        for i, (sid, _, kind, ts, text, asks, _d, nh) in enumerate(xs):
            if kind != "reply" or nh < 3 or (asks or 0) < 0.8 or (ts or "") < since:
                continue
            nu = next((x for x in xs[i + 1:] if x[2] == "user"), None)
            if (nu is None or (nu[6] or 0) < 0.6) or (text or "").count("?") >= 3:
                keep.append((sid,))
    db.executemany("insert or ignore into cands values (?, 'asks-tim-mid')", keep)
    db.commit()
    for r in db.execute("select why, count(*) from cands group by 1 order by 2 desc"):
        print(*r, file=sys.stderr)
    print("total", db.execute("select count(*) from cands").fetchone()[0], file=sys.stderr)


if __name__ == "__main__":
    main()
