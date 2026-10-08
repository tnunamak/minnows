"""Segment native agent sessions (via the convo ledger) into candidate units.

Read-only on the convo ledger. Writes table `segments` in the state database.
Idempotent: segment ids are stable hashes of (session key, ordinal, kind).

Segment kinds:
  user   - a message that passes cheap "maybe human-typed" rules; carries the
           preceding assistant final reply as context (for "yes, do option 2").
  reply  - the assistant's final reply to a `user` segment (last assistant
           message before the next user message).
  tail   - the last assistant message of a session, for every session (also
           automated lanes), plus whether the last turn was left unanswered.
"""
import hashlib, re, sqlite3, sys
from uf_config import CFG, CONVO_LEDGER, WORK_DB, since_default

CONVO = str(CONVO_LEDGER)
SINCE = sys.argv[1] if len(sys.argv) > 1 else since_default()

# User turns that software or another agent wrote. Owner-specific prefixes go in
# config.json "automated_prefixes".
AUTO_PREFIX = re.compile(
    r"^(\s*[<\[{#]|Delegated task|You are |Your brief|Read the file |Read and do|"
    r"Act as |Review PR|This session is being continued|Caveat:|Base directory for this skill|"
    r"Update: |Task:|TASK:|Implement |Continue from|Status check:|Heartbeat|Wake"
    + "".join("|" + p for p in CFG["automated_prefixes"]) + ")", re.I)
AUTO_SUBSTR = ("task-notification", "<command-name>", "reached a terminal state",
               "system-reminder", "Request interrupted by user")


def maybe_human(text, is_first):
    t = text.strip()
    if len(t) < 2 or len(t) > 4000:
        return False
    if AUTO_PREFIX.match(t) or any(s in t for s in AUTO_SUBSTR):
        return False
    if is_first and len(t) > 1500:  # long opening briefs are almost always generated
        return False
    return True


def sid(*parts):
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:16]


def main():
    src = sqlite3.connect(f"file:{CONVO}?mode=ro", uri=True)
    db = sqlite3.connect(str(WORK_DB), timeout=600)
    db.executescript("""
    create table if not exists segments (
      seg_id text primary key, kind text, harness text, session_key text, source_path text,
      project text, ts text, ordinal int, text text, context text, unanswered int default 0,
      human_rule int, next_ts text);
    create table if not exists sessions (
      session_key text primary key, harness text, source_path text, project text,
      first_ts text, last_ts text, n_msgs int, n_human int);
    """)
    # one source per (harness, session_id): prefer the one with the most messages
    rows = src.execute("""
      select s.id, s.harness, s.session_id, s.path, s.project, count(m.id) n, max(m.message_ts) last
      from source_files s join messages m on m.source_id=s.id
      group by s.id having last >= ?""", (SINCE,)).fetchall()
    best = {}
    for r in rows:
        key = f"{r[1]}:{r[2] or r[3]}"
        if key not in best or r[5] > best[key][5]:
            best[key] = r
    print(f"sources in window: {len(rows)}; unique sessions: {len(best)}", file=sys.stderr)
    nseg = 0
    for key, (src_id, harness, _s, path, project, n, last) in best.items():
        msgs = src.execute("select ordinal, role, text, message_ts from messages where source_id=? order by ordinal",
                           (src_id,)).fetchall()
        # group into turns: user message followed by assistant messages
        last_final = ""
        first_user_seen = False
        n_human = 0
        segs = []
        i = 0
        while i < len(msgs):
            o, role, text, ts = msgs[i]
            if role == "user":
                j = i + 1
                final = None
                while j < len(msgs) and msgs[j][1] == "assistant":
                    final = msgs[j]
                    j += 1
                human = maybe_human(text, not first_user_seen)
                first_user_seen = True
                if human and (ts or "") >= SINCE:
                    n_human += 1
                    segs.append((sid(key, o, "user"), "user", o, ts, text[:4000], last_final[-1500:], int(final is None), None))
                    if final is not None:
                        segs.append((sid(key, final[0], "reply"), "reply", final[0], final[3], final[2][:6000], text[:1500], 0,
                                     msgs[j][3] if j < len(msgs) else None))
                if final is not None:
                    last_final = final[2]
                i = j
            else:
                i += 1
        lo, lrole, ltext, lts = msgs[-1]
        last_a = next((m for m in reversed(msgs) if m[1] == "assistant"), None)
        if last_a is not None:
            prev_user = next((m for m in reversed(msgs[: msgs.index(last_a)]) if m[1] == "user"), None)
            segs.append((sid(key, last_a[0], "tail"), "tail", last_a[0], last_a[3], last_a[2][:6000],
                         (prev_user[2][:1500] if prev_user else ""), int(lrole == "user"), None))
        db.execute("insert or replace into sessions values (?,?,?,?,?,?,?,?)",
                   (key, harness, path, project, msgs[0][3], last, len(msgs), n_human))
        for s in segs:
            db.execute("""insert or replace into segments (seg_id, kind, harness, session_key, source_path, project,
                          ts, ordinal, text, context, unanswered, human_rule, next_ts) values (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (s[0], s[1], harness, key, path, project, s[3], s[2], s[4], s[5], s[6], int(s[1] == "user"), s[7]))
            nseg += 1
    db.commit()
    for k, c in db.execute("select kind, count(*) from segments group by 1"):
        print(k, c, file=sys.stderr)


if __name__ == "__main__":
    main()
