"""R2 + R3: deterministic PR state and cross-session text evidence per item.

- PR refs must be repo-qualified (owner/repo#n, repo#n, or "#n" resolved through
  the item's repo). State comes from `gh pr view` (read-only, cached).
- Text evidence: FTS over later messages for the item's rare identifiers, scoped
  to messages after the item's timestamp. Evidence is a hint for the
  adjudicator; it never closes an item by itself.
Writes table `evidence(item_id, prs json, snippets json)`.
"""
import glob, json, os, re, sqlite3, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from uf_config import CFG, CONVO_LEDGER, WORK_DB

CONVO = str(CONVO_LEDGER)
STOP = set("""about after again agent before branch check commit commits change changes connector connectors
data decide default deploy design final first fixed fixes follow merge merged needs next open other report
review should still tests their there these thing those update which while would write written""".split())


def repo_map():
    m = {}
    for d in [d for root in CFG["repo_roots"] for d in glob.glob(os.path.expanduser(root) + "/*")]:
        try:
            url = subprocess.run(["git", "-C", d, "remote", "get-url", "origin"], capture_output=True, text=True,
                                 timeout=5).stdout.strip()
        except Exception:
            continue
        mm = re.search(r"github\.com[:/]([^/]+/[^/.]+)", url)
        if mm:
            m.setdefault(os.path.basename(d).lower(), mm.group(1))
            m.setdefault(mm.group(1).split("/")[1].lower(), mm.group(1))
    return m


PR_FULL = re.compile(r"\b([A-Za-z0-9-]+/[A-Za-z0-9._-]+)#(\d{1,5})\b")
PR_SHORT = re.compile(r"\b([A-Za-z][A-Za-z0-9._-]{2,})#(\d{1,5})\b")
PR_BARE = re.compile(r"(?:PR|pr|pull request)\s*#?(\d{1,5})\b|(?<![\w/])#(\d{2,5})\b")
IDENT = re.compile(r"`([^`\s]{5,60})`|\b([a-z]+_[a-z0-9_]{3,}|[a-z]+[A-Z][A-Za-z0-9]{3,}|[\w-]+\.(?:md|ts|tsx|py|sh|json|yaml|yml|rs|go))\b")


def pr_refs(text, repo, rmap):
    out = set()
    for full, n in PR_FULL.findall(text):
        out.add((full, int(n)))
    for short, n in PR_SHORT.findall(text):
        full = rmap.get(short.lower())
        if full:
            out.add((full, int(n)))
    base = rmap.get((repo or "").lower())
    if base:
        for a, b in PR_BARE.findall(text):
            out.add((base, int(a or b)))
    return sorted(out)


def gh_state(ref):
    full, n = ref
    r = subprocess.run(["gh", "pr", "view", str(n), "-R", full, "--json", "state,title,mergedAt,closedAt,updatedAt,isDraft,url"],
                       capture_output=True, text=True, timeout=60)
    try:
        return ref, json.loads(r.stdout)
    except Exception:
        return ref, {"error": (r.stderr or "")[:120]}


def main():
    db = sqlite3.connect(str(WORK_DB), timeout=600)
    db.execute("create table if not exists prs (ref text primary key, data text)")
    db.execute("create table if not exists evidence (item_id text primary key, prs text, snippets text)")
    rmap = repo_map()
    items = db.execute("select item_id, source_type, project, title, detail, quote, ts, session_key from items").fetchall()
    refs_by_item, all_refs = {}, set()
    for iid, st, project, title, detail, quote, ts, sk in items:
        text = " ".join(x or "" for x in (title, detail, quote))
        if st == "pr":
            m = re.search(r"PR ([^#\s]+)#(\d+)", title or "") or re.search(r"requested: ([^#\s]+)#(\d+)", title or "")
            refs = [(m.group(1), int(m.group(2)))] if m else []
        else:
            refs = pr_refs(text, project, rmap)
        refs_by_item[iid] = refs[:6]
        all_refs.update(refs[:6])
    known = {r[0] for r in db.execute("select ref from prs")}
    todo = [r for r in all_refs if f"{r[0]}#{r[1]}" not in known]
    print(f"items={len(items)} pr refs={len(all_refs)} to fetch={len(todo)}", file=sys.stderr)
    with ThreadPoolExecutor(6) as ex:
        for ref, data in ex.map(gh_state, todo):
            db.execute("insert or replace into prs values (?,?)", (f"{ref[0]}#{ref[1]}", json.dumps(data)))
    db.commit()
    src = sqlite3.connect(f"file:{CONVO}?mode=ro", uri=True)
    for iid, st, project, title, detail, quote, ts, sk in items:
        prs = []
        for full, n in refs_by_item[iid]:
            row = db.execute("select data from prs where ref=?", (f"{full}#{n}",)).fetchone()
            prs.append({"ref": f"{full}#{n}", **(json.loads(row[0]) if row else {})})
        snippets = []
        if st == "session" and ts:
            text = " ".join(x or "" for x in (title, quote, (json.loads(detail or "{}").get("object") or "")))
            idents = []
            for a, b in IDENT.findall(text):
                t = (a or b).strip(".,:;()")
                if len(t) >= 5 and t.lower() not in STOP and t not in idents:
                    idents.append(t)
            for t in idents[:4]:
                q = " ".join('"' + w + '"' for w in re.findall(r"\w+", t) if w)
                if not q:
                    continue
                try:
                    rows = src.execute("""select m.text, m.message_ts, m.role, s.session_id, s.harness from messages_fts f
                        join messages m on m.id=f.rowid join source_files s on s.id=m.source_id
                        where messages_fts match ? and m.message_ts > ? order by m.message_ts limit 40""",
                                       (q, ts)).fetchall()
                except sqlite3.OperationalError:
                    continue
                seen = set()
                for txt, mts, role, sess, harn in rows:
                    key = f"{harn}:{sess}"
                    if key in seen:
                        continue
                    seen.add(key)
                    i = max(0, (txt or "").find(re.findall(r"\w+", t)[0]) - 250)
                    snippets.append({"term": t, "ts": mts, "role": role, "session": key,
                                     "same_session": key == sk, "text": " ".join((txt or "")[i:i + 600].split())})
                    if len([s for s in snippets if s["term"] == t]) >= 3:
                        break
        db.execute("insert or replace into evidence values (?,?,?)", (iid, json.dumps(prs), json.dumps(snippets[:10])))
    db.commit()
    print("done", file=sys.stderr)


if __name__ == "__main__":
    main()
