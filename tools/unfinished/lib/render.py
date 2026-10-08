"""Render the ledger: LEDGER.md (for the owner), ledger.jsonl (for tools), and one
evidence page per item (one click from the ledger to the source excerpt).

Owner decisions (decisions.jsonl, append-only) always win over pipeline
verdicts. Ticked boxes in the previous LEDGER.md are imported as `done`
decisions, but only if that file's generation id matches the last render, so a
stale copy cannot overwrite newer state.
"""
import datetime as dt, json, os, re, sqlite3, sys, uuid
from collections import defaultdict
from uf_config import DATA, EVIDENCE, STATE, WORK_DB

OUT = str(DATA)
EV = str(EVIDENCE)
DEC = os.path.join(OUT, "decisions.jsonl")
GEN = os.path.join(OUT, ".generation")
NOW = dt.datetime.now().astimezone()


def load_decisions():
    d = {}
    if os.path.exists(DEC):
        for line in open(DEC):
            try:
                e = json.loads(line)
                d[e["id"]] = e
            except Exception:
                pass
    return d


def import_ticks():
    p = os.path.join(OUT, "LEDGER.md")
    if not (os.path.exists(p) and os.path.exists(GEN)):
        return 0
    txt = open(p).read()
    m = re.search(r"<!-- generation: (\S+) -->", txt)
    if not m or m.group(1) != open(GEN).read().strip():
        print("LEDGER.md generation mismatch; not importing ticks", file=sys.stderr)
        return 0
    dec = load_decisions()
    n = 0
    lines = txt.splitlines()
    ticked = []
    for k, line in enumerate(lines):  # "- [x] ... `id`", "1. [x] ... `id`", or "### 1. [x] ..." with the id on the next line
        if not re.match(r"^\s*(?:#+\s*)?(?:\d+\.\s*)?(?:- )?\[[xX]\]", line):
            continue
        m = re.search(r"`((?:s|pr|inbox|miss|review|wt|lane|review-req):[^`]+)`", line) or \
            (k + 1 < len(lines) and re.search(r"^`((?:s|pr|inbox|miss|review|wt|lane|review-req):[^`]+)`", lines[k + 1]))
        if m:
            ticked.append(m.group(1))
    with open(DEC, "a") as f:
        for iid in dict.fromkeys(ticked):
            if iid not in dec:
                f.write(json.dumps({"id": iid, "decision": "done", "at": NOW.isoformat(), "via": "ledger-tick"}) + "\n")
                n += 1
    return n


def links_for(it, t3_by_session):
    out = []
    for p in it["prs"]:
        if p.get("url"):
            out.append(f"[{p['ref']} ({(p.get('state') or '').lower()})]({p['url']})")
    for l in it["links"]:
        if l.startswith("http"):
            out.append(f"[PR]({l})" if "/pull/" in l else f"[link]({l})")
        elif l.startswith("t3-thread://"):
            out.append(f"[T3 thread]({l})")
    t3 = t3_by_session.get(it.get("session_key") or "")
    if t3:
        out.append(f"[T3 thread]({t3})")
    out.append(f"[evidence](evidence/{it['slug']}.md)")
    return " · ".join(dict.fromkeys(out))


def main():
    os.makedirs(EV, exist_ok=True)
    ticked = import_ticks()
    dec = load_decisions()
    db = sqlite3.connect(str(WORK_DB), timeout=600)
    t3_by_session = {sk: url for sk, url in db.execute(
        "select session_key, url from t3threads where session_key is not null")}
    rows = db.execute("""select i.item_id, i.source_type, i.kind, i.project, i.title, i.detail, i.owner, i.ts, i.last_ts,
                         i.status_reason, i.links, i.quote, i.session_key, i.seg_id, p.score, p.bundle, a.out
                         from items i join prerank p using(item_id) left join adj a using(item_id)""").fetchall()
    resolved = {}
    if db.execute("select 1 from sqlite_master where name='resolved'").fetchone():
        resolved = {i: json.loads(o) for i, o in db.execute("select item_id, out from resolved")}
    items, bundles = [], defaultdict(list)
    for (iid, st, kind, project, title, detail, owner, ts, last_ts, sreason, links, quote, sk, seg_id, score,
         bundle, adj) in rows:
        a = json.loads(adj) if adj else None
        ev = db.execute("select prs from evidence where item_id=?", (iid,)).fetchone()
        prs = json.loads(ev[0]) if ev and ev[0] else []
        it = {"id": iid, "source_type": st, "kind": kind, "project": project or "misc", "title": title,
              "owner": owner, "first_seen": (ts or "")[:10], "last_activity": (last_ts or "")[:10],
              "status_note": sreason, "links": json.loads(links or "[]"), "quote": quote, "session_key": sk,
              "seg_id": seg_id, "score": score, "prs": prs,
              "slug": re.sub(r"[^A-Za-z0-9._-]+", "_", iid)[:90]}
        if a:
            it.update({k: a.get(k) for k in ("verdict", "verdict_reason", "needs_owner", "next_action", "why",
                                              "priority", "effort", "duplicate_of", "confidence")})
        r = resolved.get(iid)
        if r:  # an agent with tools (or a hand check) looked at the current state: this wins over model verdicts
            st_now = r.get("status_now")
            it["verdict"] = {"open": "open", "unclear": "open"}.get(st_now, st_now)
            it["unclear"] = st_now == "unclear"
            it["checked"] = r.get("evidence") or r.get("note") or ""
            it["verdict_reason"] = it["checked"]
            it["confidence"] = "checked"
            m = re.search(r"(?i)\b(?:same as|duplicate of)\s+`?((?:s|pr|inbox|miss|review|wt|lane):[^\s`,;)]+?)[:.]?(?=[\s`,;)]|$)", it["checked"])
            if m and m.group(1) != iid:
                it["duplicate_of"] = m.group(1)
            for k in ("next_action", "why", "needs_owner", "priority", "effort"):
                if r.get(k) not in (None, ""):
                    it[k] = r[k]
            a = a or r
        if bundle and not a:
            bundles[bundle].append(it)
            continue
        if not a:
            continue  # not adjudicated: kept in ledger.jsonl only
        d = dec.get(iid)
        if d:
            it["owner_decision"] = d
        items.append(it)
    by_id = {i["id"]: i for i in items}
    # Duplicate links, lowest precedence first: same-batch marks from the adjudicator, Jev pair check,
    # the resolver's "same as" notes, then the Sonnet clustering of checked items (whose survivors are roots).
    dup_of = {i["id"]: i["duplicate_of"] for i in items if i.get("duplicate_of")}
    dup_of.update({d: s for d, s in db.execute("select item_id, survivor from dups")})
    dup_of.update({i["id"]: i["duplicate_of"] for i in items if i.get("checked") and i.get("duplicate_of")})
    if db.execute("select 1 from sqlite_master where name='top_clusters'").fetchone():
        for d, sv, merged in db.execute("select item_id, survivor, merged_action from top_clusters"):
            if d != sv:
                dup_of[d] = sv
            else:
                dup_of.pop(sv, None)
                if sv in by_id and merged:
                    by_id[sv]["next_action"] = merged

    def root(x):
        seen = set()
        while x in dup_of and x not in seen and dup_of[x] in by_id:
            seen.add(x)
            x = dup_of[x]
        return x

    live_verdicts = ("open", "in-flight")
    for i in items:  # collapse each duplicate into the root of its chain; never hide a live item under a dead one
        r = root(i["id"])
        if r == i["id"]:
            continue
        tgt = by_id[r]
        if i.get("verdict") in live_verdicts and tgt.get("verdict") not in live_verdicts:
            continue
        tgt.setdefault("also_seen", []).append(i["id"])
        i["verdict"] = "duplicate"
        i["duplicate_of"] = r
    cutoff = (NOW - dt.timedelta(hours=36)).strftime("%Y-%m-%d")
    for i in items:  # fresh agent-owned work is probably still in flight: show it, but not in the top
        if i.get("verdict") == "open" and not i.get("checked") and not i.get("needs_owner") \
                and (i["last_activity"] or i["first_seen"]) >= cutoff:
            i["verdict"] = "in-flight"

    def rank(i):  # checked-and-still-open first, then priority, then "needs you", then the cheap score
        return (-int(bool(i.get("checked")) and not i.get("unclear")), -(i.get("priority") or 0),
                -int(bool(i.get("needs_owner"))), -(i.get("score") or 0))

    live = [i for i in items if i.get("verdict") == "open" and not i.get("owner_decision")]
    snoozed = [i for i in items if (i.get("owner_decision") or {}).get("decision") == "snooze"
               and (i["owner_decision"].get("until") or "") > NOW.isoformat()]
    live += [i for i in items if (i.get("owner_decision") or {}).get("decision") == "snooze"
             and (i["owner_decision"].get("until") or "") <= NOW.isoformat() and i.get("verdict") == "open"]
    live.sort(key=rank)
    likely = sorted([i for i in items if i.get("verdict") == "likely-done" and not i.get("owner_decision")], key=rank)
    stale = sorted([i for i in items if i.get("verdict") in ("stale", "superseded") and not i.get("owner_decision")], key=rank)
    closed = [i for i in items if (i.get("owner_decision") or {}).get("decision") in ("done", "drop")]
    found_done = sorted([i for i in items if i.get("checked") and i.get("verdict") == "done"], key=rank)

    # evidence pages
    for i in items + [x for b in bundles.values() for x in b]:
        write_evidence(db, i)

    gen = uuid.uuid4().hex[:12]
    L = []
    w = L.append
    w("# Unfinished business\n")
    w(f"<!-- generation: {gen} -->")
    w(f"Generated {NOW:%Y-%m-%d %H:%M %Z} from the last 30 days of agent sessions (Claude, Codex), T3 threads, "
      f"open PRs, waspflow lanes and inbox notes.\n")
    n_checked = sum(1 for i in live if i.get("checked"))
    w(f"- **{n_checked} items were checked against git, GitHub and later sessions and are still open.** The Top 25 come from these.")
    w(f"- {len(found_done)} items that looked open turned out to be finished when checked (listed below, nothing to do).")
    w(f"- {len(live) - n_checked} more candidates are not checked yet. In a hand-checked sample only about 1 in 4 such items "
      f"was still open, so treat that section as a list to skim, not a to-do list.")
    w(f"- {len(likely)} likely done and {len(stale)} probably stale items are collapsed at the bottom."
      f"{' Imported ' + str(ticked) + ' ticks from the last ledger.' if ticked else ''}\n")
    w("**How to use:** tick a box (`[x]`) to close an item; the next run records it. Or run "
      "`unfinished done|snooze|drop <id> [7d|note]`. Every item has an *evidence* link to the exact source excerpt.\n")
    top = live[:25]
    start = [i for i in top if i.get("needs_owner")][:3] or top[:3]
    w("## Start here\n")
    for n, i in enumerate(start, 1):
        w(f"{n}. [ ] **{i['next_action']}** — {i['why']} `{i['id']}`")
    w("\n## Top 25\n")
    for n, i in enumerate(top, 1):
        who = "you" if i.get("needs_owner") else "an agent"
        w(f"### {n}. [ ] {i['next_action']}")
        w(f"`{i['id']}` · **{i['project']}** · P{i.get('priority')} · {i.get('effort')} · needs {who}")
        w(f"- **Why:** {i['why']}")
        if i.get("checked"):
            w(f"- **Checked {NOW:%m-%d}:** {'unclear, ' if i.get('unclear') else 'still open: '}{i['checked']}")
        else:
            w(f"- **Verdict:** open, not yet checked against current state ({i.get('confidence')} confidence): "
              f"{i.get('verdict_reason')}")
        w(f"- **Seen:** {i['first_seen']}" + (f", again in {len(i['also_seen'])} other place(s)" if i.get("also_seen") else "")
          + (f" · last activity {i['last_activity']}" if i['last_activity'] and i['last_activity'] != i['first_seen'] else ""))
        if i.get("quote"):
            w(f"- **Source says:** “{i['quote'][:220]}”")
        w(f"- **Links:** {links_for(i, t3_by_session)}\n")
    more_checked = [i for i in live[25:] if i.get("checked")]
    if more_checked:
        w(f"## Also checked and still open ({len(more_checked)})\n")
        for i in more_checked:
            w(f"- [ ] **{i['next_action']}** — {i['why']} · **{i['project']}** · P{i.get('priority')}"
              f"{' · needs you' if i.get('needs_owner') else ''}{' · unclear' if i.get('unclear') else ''} · "
              f"{links_for(i, t3_by_session)} `{i['id']}`")
        w("")
    # Parked projects: repos whose open PRs have all gone quiet for 30+ days. One decision each, not N rows.
    parked = defaultdict(list)
    quiet = (NOW - dt.timedelta(days=14)).strftime("%Y-%m-%d")
    for i in [x for x in items if x["source_type"] == "pr" and x["kind"] == "open-pr"] + bundles.get("abandoned-prs", []):
        m = re.match(r"PR ([^#\s]+)#", i["title"] or "")
        if m:
            parked[m.group(1)].append(i)
    parked = {r: xs for r, xs in parked.items()
              if len(xs) >= 2 and max(x["last_activity"] or "" for x in xs) < quiet}
    if parked:
        w(f"## Parked projects: revive or shelve? ({len(parked)})\n")
        w("Repos with 2+ open PRs and none touched in the last 14 days. Decide once per repo: pick one PR to revive, or close them all.\n")
        for r, xs in sorted(parked.items(), key=lambda kv: -len(kv[1])):
            last = max(x["last_activity"] or "" for x in xs)
            wts = [i for i in items if i["source_type"] == "worktree" and r.split("/")[-1] in (i["project"] or "")]
            w(f"- [ ] **{r}**: {len(xs)} open PRs, last touched {last}"
              + (f", plus {len(wts)} local checkout(s) with unpushed commits" if wts else "") + " · "
              + ", ".join(f"[#{x['id'].rsplit('#', 1)[-1]}]({x['links'][0]})" for x in
                          sorted(xs, key=lambda x: x["last_activity"] or "", reverse=True)[:8]))
        w("")
    rest = [i for i in live[25:] if not i.get("checked")]
    byp = defaultdict(list)
    for i in rest:
        byp[i["project"]].append(i)
    w(f"## Not yet checked, by project ({len(rest)})\n")
    w("Candidates from the transcripts that no agent has checked against the current state yet. Most are already done. "
      "The nightly run checks the highest-ranked ones and moves survivors up.\n")
    for p in sorted(byp, key=lambda p: rank(byp[p][0])):
        w(f"### {p} ({len(byp[p])})\n")
        for i in byp[p][:6]:
            w(f"- [ ] **{i['next_action']}** — {i['why']} · P{i.get('priority')}{' · needs you' if i.get('needs_owner') else ''} · "
              f"{i['first_seen']} · {links_for(i, t3_by_session)} `{i['id']}`")
        if len(byp[p]) > 6:
            w(f"- … and {len(byp[p]) - 6} more lower-ranked items: `unfinished list {p}`")
        w("")
    inflight = sorted([i for i in items if i.get("verdict") == "in-flight" and not i.get("owner_decision")], key=rank)
    w(f"## In flight: agent work from the last 36 hours ({len(inflight)})\n")
    w("Probably still being worked on. Shown so nothing is lost; check again tomorrow.\n")
    w("<details><summary>Show</summary>\n")
    for i in inflight:
        w(f"- [ ] {i['next_action']} · **{i['project']}** · P{i.get('priority')} · {links_for(i, t3_by_session)} `{i['id']}`")
    w("\n</details>\n")
    w(f"## Found done when checked ({len(found_done)})\n")
    w("These looked open in the transcripts, but checking git, PRs and later sessions showed them finished. Nothing to do.\n")
    w("<details><summary>Show</summary>\n")
    for i in found_done:
        w(f"- ~~{i.get('next_action') or i['title']}~~ — {i['checked']} `{i['id']}`")
    w("\n</details>\n")
    w(f"## Likely done: glance and tick ({len(likely)})\n")
    w("<details><summary>Show</summary>\n")
    for i in likely:
        w(f"- [ ] {i['next_action']} — *{i.get('verdict_reason')}* · {links_for(i, t3_by_session)} `{i['id']}`")
    w("\n</details>\n")
    w(f"## Probably stale or superseded ({len(stale)})\n")
    w("<details><summary>Show</summary>\n")
    for i in stale:
        w(f"- [ ] {i['next_action']} — *{i.get('verdict')}: {i.get('verdict_reason')}* · {links_for(i, t3_by_session)} `{i['id']}`")
    w("\n</details>\n")
    w("## Hygiene bundles\n")
    names = {"dead-prs-old": "Open PRs untouched for 9+ months: close in one pass",
             "abandoned-prs": "Open PRs untouched for 30+ days: close, or revive one",
             "unclosed-lanes": "waspflow lanes from the last 30 days that never got a closeout",
             "old-review-requests": "Review requests older than three months"}
    for b, xs in sorted(bundles.items()):
        w(f"<details><summary><b>{names.get(b, b)}</b> ({len(xs)})</summary>\n")
        for i in sorted(xs, key=lambda x: x["last_activity"] or "", reverse=True):
            link = next((l for l in i["links"] if l.startswith("http")), i["links"][0] if i["links"] else "")
            w(f"- [ ] {i['title'][:150]} · {i['status_note'] or ''} · {('[link](' + link + ')') if link.startswith('http') else '`' + link + '`'} `{i['id']}`")
        w("\n</details>\n")
    if snoozed or closed:
        w(f"## Snoozed ({len(snoozed)}) and closed by you ({len(closed)})\n")
        for i in snoozed:
            w(f"- {i['next_action']} — snoozed until {i['owner_decision'].get('until', '')[:10]} `{i['id']}`")
        for i in closed:
            w(f"- ~~{i['next_action']}~~ — {i['owner_decision']['decision']} {i['owner_decision'].get('note', '')} `{i['id']}`")
        w("")
    w("## Coverage\n")
    cov = STATE / "coverage.md"  # owner-written notes on what is and is not mined
    w(cov.read_text() if cov.exists() else "Not described: add state/coverage.md to the data folder.\n")
    tmp = os.path.join(OUT, "LEDGER.md.tmp")
    open(tmp, "w").write("\n".join(L) + "\n")
    os.replace(tmp, os.path.join(OUT, "LEDGER.md"))
    open(GEN, "w").write(gen)
    with open(os.path.join(OUT, "ledger.jsonl.tmp"), "w") as f:
        for i in sorted(items, key=rank) + [x for b in bundles.values() for x in b]:
            f.write(json.dumps(i, ensure_ascii=False) + "\n")
    os.replace(os.path.join(OUT, "ledger.jsonl.tmp"), os.path.join(OUT, "ledger.jsonl"))
    print(f"open={len(live)} likely={len(likely)} stale={len(stale)} bundles={ {k: len(v) for k, v in bundles.items()} }",
          file=sys.stderr)


def write_evidence(db, i):
    from jev import redact
    L = [f"# {i.get('next_action') or i['title']}\n", f"`{i['id']}` · {i['project']} · source: {i['source_type']} · first seen {i['first_seen']}\n"]
    if i.get("verdict"):
        L.append(f"**Verdict:** {i['verdict']} ({i.get('confidence')}): {i.get('verdict_reason')}\n")
    L.append(f"**Extracted item:** {i['title']}\n")
    if i.get("session_key"):
        sk = i["session_key"]
        L.append(f"**Session:** `{sk}` · open it with `convo show {sk.split(':', 1)[1]} --all-projects` "
                 f"(or `convo search` for a phrase from the quote)\n")
    if i.get("seg_id"):
        seg = db.execute("select kind, ts, text, context from segments where seg_id=?", (i["seg_id"],)).fetchone()
        if seg:
            kind, ts, text, ctx = seg
            L.append(f"## Source excerpt ({kind}, {ts})\n")
            if ctx:
                L.append("**Before:**\n\n> " + redact(ctx[-1500:]).replace("\n", "\n> ") + "\n")
            L.append("**At this point:**\n\n> " + redact(text[:5000]).replace("\n", "\n> ") + "\n")
    ev = db.execute("select prs, snippets from evidence where item_id=?", (i["id"],)).fetchone()
    if ev:
        prs, sn = json.loads(ev[0] or "[]"), json.loads(ev[1] or "[]")
        if prs:
            L.append("## Referenced PRs\n")
            for p in prs:
                L.append(f"- {p.get('ref')}: {p.get('state')} {p.get('title') or ''} {p.get('url') or ''}")
            L.append("")
        if sn:
            L.append("## Later mentions elsewhere\n")
            for s in sn:
                L.append(f"- {s['ts'][:16]} {s['role']} in `{s['session']}`{' (same session)' if s['same_session'] else ''}: "
                         f"{redact(s['text'])[:500]}")
            L.append("")
    if i.get("links"):
        L.append("## Links\n")
        for l in i["links"]:
            L.append(f"- {l}")
    open(os.path.join(EV, f"{i['slug']}.md"), "w").write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
