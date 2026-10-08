"""Pass 1: cheap Jev nouls over every segment. Stores raw probabilities in `jev1`.

Raw judgments are kept separate from policy (thresholds live in select.py), so
retuning a threshold never needs a rerun.
"""
import json, sqlite3, sys, time
import jev
from uf_config import WORK_DB


USER_Q = {
    "human": {"type": "noul",
        "instructions": "Was `message` typed by a human person chatting with an AI assistant?",
        "criteria": {"true": "A person wrote it: conversational, often terse or informal, may have typos, reacts to the assistant.",
                     "false": "Software or another AI agent produced it: a generated task brief, a status notification, a log, a template, or pasted tool output with no personal comment."}},
    "work_intent": {"type": "noul",
        "instructions": "In `message`, does the person ask for, decide on, or state a plan for work that someone still has to carry out (code, config, a fix, research, a document, or applying a decision)?",
        "criteria": {"true": "It requests or commits to concrete work or a decision that must be applied.",
                     "false": "It only asks for information or status, acknowledges, thanks, or chats."}},
    "deferral": {"type": "noul",
        "instructions": "Does `message` explicitly put some work off until later, for example with 'later', 'after X ships', 'hold off', 'not now', 'next', 'tomorrow', 'eventually', 'remind me', or 'park this'?"},
    "decision": {"type": "noul",
        "instructions": "Does `message` answer a question from `previous_assistant_reply` by choosing an option, approving, or rejecting something?"},
}

REPLY_Q = {
    "open_work": {"type": "noul",
        "instructions": "Does `reply` say that some work is still not done: remaining steps, follow-ups, next steps, blocked items, deferred items, or work left for later?",
        "criteria": {"true": "At least one concrete piece of work is reported as not yet done.",
                     "false": "Nothing is reported as still to do."}},
    "asks_human": {"type": "noul",
        "instructions": "Does `reply` ask the human to decide, approve, confirm, or provide something before some work can continue?"},
    "promise": {"type": "noul",
        "instructions": "Does `reply` say the assistant will do something in the future that it has not already done in this reply?"},
    "complete": {"type": "noul",
        "instructions": "Does `reply` report that all of the requested work is finished, with nothing left open?"},
    "interrupted": {"type": "noul",
        "instructions": "Does `reply` show the assistant was cut off or failed before finishing, for example a usage limit, an API error, a crash, or a timeout?"},
}


def state_for(kind, text, ctx):
    if kind == "user":
        return {"previous_assistant_reply": ctx[-1500:], "message": text[:4000]}
    return {"human_request": ctx[:1500], "reply": text[:6000]}


def main():
    db = sqlite3.connect(str(WORK_DB), timeout=600)
    db.execute("create table if not exists jev1 (seg_id text primary key, answers text)")
    where = sys.argv[1] if len(sys.argv) > 1 else "1=1"
    rows = db.execute(f"""select seg_id, kind, text, context from segments
                          where seg_id not in (select seg_id from jev1) and {where}""").fetchall()
    print(f"to score: {len(rows)}", file=sys.stderr)
    items = [(r[0], (r[1], state_for(r[1], r[2], r[3] or ""))) for r in rows]
    kinds = {r[0]: r[1] for r in rows}
    t0 = time.time()
    B = 2000
    for b in range(0, len(items), B):
        chunk = items[b:b + B]
        res = jev.ask_many([(i, s[1]) for i, s in chunk],
                           lambda i, st: USER_Q if kinds[i] == "user" else REPLY_Q, workers=48)
        for i, a in res.items():
            if "_error" not in a:
                db.execute("insert or replace into jev1 values (?,?)", (i, json.dumps(a)))
        db.commit()
        print(f"{b + len(chunk)}/{len(items)} {time.time() - t0:.0f}s usage={jev.usage}", file=sys.stderr)


if __name__ == "__main__":
    main()
