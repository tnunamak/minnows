"""Ingest sources that are already item-shaped: open PRs and review requests,
waspflow lanes, inbox notes, misses tables, "## Open" sections in notes, and
local unpushed work. Which files to read comes from config.json in the data
folder; every source is optional.

Read-only. Writes table `items` rows with source_type in
{pr, lane, inbox, miss, review, worktree}. Ids are stable per source.
"""
import glob, json, os, re, sqlite3, subprocess, sys, time
from uf_config import CFG, WORK_DB, since_default

H = os.path.expanduser
SINCE_EPOCH = time.time() - CFG["window_days"] * 86400
DEFAULT_WASPFLOW_HOME = H("~/.local/state/waspflow")

SCHEMA = """create table if not exists items (
  item_id text primary key, source_type text, kind text, project text, title text, detail text,
  owner text, ts text, last_ts text, status text, status_reason text, evidence text, links text,
  raw text, session_key text, seg_id text, quote text)"""


def put(db, **r):
    cols = ["item_id", "source_type", "kind", "project", "title", "detail", "owner", "ts", "last_ts", "status",
            "status_reason", "evidence", "links", "raw", "session_key", "seg_id", "quote"]
    db.execute(f"insert or replace into items ({','.join(cols)}) values ({','.join('?' * len(cols))})",
               [r.get(c) if not isinstance(r.get(c), (list, dict)) else json.dumps(r.get(c)) for c in cols])


def gh_json(args):
    return json.loads(subprocess.run(["gh", *args], capture_output=True, text=True, check=True, timeout=120).stdout)


def scan_worktrees():
    """Local checkouts with commits that are on no remote (untracked files alone are noise)."""
    out = []
    for d in [d for root in CFG["repo_roots"] for d in glob.glob(H(root) + "/*")]:
        if not os.path.exists(os.path.join(d, ".git")):
            continue
        g = lambda *a: subprocess.run(["git", "-C", d, *a], capture_output=True, text=True, timeout=20).stdout.strip()
        try:
            n = int(g("rev-list", "--count", "HEAD", "--not", "--remotes") or 0)
        except ValueError:
            continue
        out.append({"path": d, "branch": g("rev-parse", "--abbrev-ref", "HEAD"), "commits_unpushed": n,
                    "upstream": g("rev-parse", "--abbrev-ref", "@{u}") or "none",
                    "uncommitted_files": len(g("status", "--porcelain", "-uno").splitlines()),
                    "last_commit": g("log", "-1", "--format=%cs")})
    return out


def live_github():
    today = time.time()
    def age(ts):
        return int((today - time.mktime(time.strptime(ts[:10], "%Y-%m-%d"))) // 86400)
    fields = "repository,number,title,url,updatedAt,createdAt,isDraft,author"
    mine = gh_json(["search", "prs", "--author", "@me", "--state", "open", "--limit", "500", "--json", fields])
    asked = gh_json(["search", "prs", "--review-requested", "@me", "--state", "open", "--limit", "200", "--json", fields])
    conv = lambda p: {"repo": p["repository"]["nameWithOwner"], "number": p["number"], "title": p["title"], "url": p["url"],
                      "created": p["createdAt"][:10], "updated": p["updatedAt"][:10], "draft": p.get("isDraft"),
                      "author": (p.get("author") or {}).get("login"), "days_since_update": age(p["updatedAt"])}
    o = [conv(p) for p in mine]
    for p in o:
        d = p["days_since_update"]
        p["class"] = "active" if d < 7 else "stale" if d <= 30 else "abandoned"
    wts = scan_worktrees()
    return {"open_prs": o, "review_requests": [conv(p) for p in asked],
            "unpushed_branches": [w for w in wts if w["commits_unpushed"]], "dirty_worktrees": []}


def prs(db):
    if not CFG["github"]:
        return
    g = live_github()
    for p in g["open_prs"]:
        cls = p.get("class")
        put(db, item_id=f"pr:{p['repo']}#{p['number']}", source_type="pr", kind="open-pr",
            project=p["repo"].split("/")[-1], title=f"PR {p['repo']}#{p['number']}: {p['title']}",
            detail=json.dumps({k: p.get(k) for k in ("draft", "review_decision", "mergeable", "checks_failed",
                                                      "checks_passed", "last_comment", "days_since_update")}),
            owner="owner", ts=p.get("created"), last_ts=p.get("updated"), status="open",
            status_reason=f"{cls}; {p.get('days_since_update')}d since update", links=[p["url"]])
    for p in g["review_requests"]:
        put(db, item_id=f"review-req:{p['repo']}#{p['number']}", source_type="pr", kind="review-request",
            project=p["repo"].split("/")[-1], title=f"Review requested: {p['repo']}#{p['number']}: {p['title']}",
            owner="owner", ts=p.get("created"), last_ts=p.get("updated"), status="open",
            status_reason=f"author {p.get('author')}; {p.get('days_since_update')}d since update", links=[p["url"]])
    for w in g.get("unpushed_branches", []) + g.get("dirty_worktrees", []):
        if (w.get("last_commit") or "") < since_default(120):
            continue
        if not w.get("commits_unpushed"):
            continue  # untracked-file noise (node_modules etc.) is not unfinished business by itself
        put(db, item_id=f"wt:{w['path']}@{w.get('branch')}", source_type="worktree", kind="unpushed-work",
            project=os.path.basename(w["path"].rstrip("/")),
            title=f"{w.get('commits_unpushed')} unpushed commits on {w.get('branch')} in {w['path'].replace(H('~'), '~')}",
            owner="owner", ts=w.get("last_commit"), last_ts=w.get("last_commit"), status="open",
            status_reason=f"upstream={w.get('upstream')}; uncommitted={w.get('uncommitted_files')}",
            links=[w["path"]])


def lanes(db):
    for home in [H(h) for h in CFG["waspflow_homes"]]:
        for f in glob.glob(f"{home}/lanes/*/state.json"):
            try:
                s = json.load(open(f))
            except Exception:
                continue
            if not s.get("prompt") or not s.get("status"):
                continue
            try:
                upd = float(s.get("updated_at") or 0)
            except ValueError:
                upd = 0
            if upd < SINCE_EPOCH:
                continue
            status, outcome, result = s.get("status"), s.get("outcome") or "", s.get("result") or ""
            if status == "reaped" or outcome in ("harvested", "superseded", "abandoned"):
                continue
            name = os.path.basename(os.path.dirname(f))
            goal = next((l.strip() for l in s["prompt"].splitlines() if l.strip()
                         and not l.startswith("WASPFLOW_LANE_MARKER") and not l.startswith("Ignore the line")), "")
            m = re.search(r"(?:Read|read)(?: the file| your brief at| and do)? (\S+\.(?:md|txt))", goal)
            if m:
                p = os.path.expanduser(m.group(1).rstrip(".,"))
                if not os.path.isabs(p):
                    p = os.path.join(s.get("cwd") or "", p)
                try:
                    goal = goal + " :: " + " ".join(open(p).read(800).split())[:400]
                except Exception:
                    pass
            put(db, item_id=f"lane:{s.get('lane_uuid') or home + ':' + name}", source_type="lane", kind="lane",
                project=os.path.basename((s.get("repo_root") or s.get("origin_cwd") or s.get("cwd") or "").rstrip("/")),
                title=f"waspflow lane {name} ({status}{', ' + result if result else ''}{', ' + (s.get('wait_state') or '') if s.get('wait_state') else ''})",
                detail=goal[:700], owner="agent", ts=time.strftime("%Y-%m-%d", time.gmtime(float(s.get("spawn_epoch") or upd))),
                last_ts=time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(upd)), status="open",
                status_reason=f"status={status} outcome={outcome or 'none'} result={result or 'none'}",
                links=[f"waspflow inspect {name}" + (f" (WASPFLOW_HOME={home})" if home != DEFAULT_WASPFLOW_HOME else ""), os.path.dirname(f)],
                session_key=(f"{s.get('provider')}:{s.get('session_id')}" if s.get("session_id") else None))


def inbox(db):
    """Notes folders: one item per markdown note. An optional triage file (a markdown
    table with "[note.md](...)" links and a **bucket** cell) drops closed buckets."""
    closed = {"done", "wont-fix", "obsolete", "obsolete-under-T3"}
    for src in CFG["inbox_dirs"]:
        d, project = H(src["dir"]), src["project"]
        buckets = {}
        if src.get("triage_file") and os.path.exists(H(src["triage_file"])):
            for line in open(H(src["triage_file"])):
                m = re.match(r"^\|\s*\d+\s*\|\s*\[([^\]]+)\]\([^)]*\)\s*\|.*\*\*([A-Za-z-]+)\*\*", line)
                if m:
                    buckets[m.group(1)] = m.group(2)
        skip = re.compile(src.get("exclude_regex") or r"(?!)")
        for f in sorted(glob.glob(f"{d}/*.md")):
            b = os.path.basename(f)
            if skip.search(b) or (src.get("triage_file") and H(src["triage_file"]) == f):
                continue
            bucket = buckets.get(b, "untriaged" if buckets else "no status field")
            if bucket in closed:
                continue
            txt = open(f).read()
            title = next((l.lstrip("# ").strip() for l in txt.splitlines() if l.strip()), b)
            ts = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(f)))
            m = re.search(r"\d{4}-\d{2}-\d{2}", b + txt[:400])
            put(db, item_id=f"inbox:{project}/{b}", source_type="inbox", kind="noticed-issue", project=project,
                title=title[:160], detail=" ".join(txt.split())[:900], owner="owner", ts=m.group(0) if m else ts,
                last_ts=ts, status="open", status_reason=f"triage bucket: {bucket}", links=[f])


def misses(db):
    """Markdown tables of tooling gaps whose last cell is a status (open, workaround, ...)."""
    for src in CFG["misses_files"]:
        f, prefix, project = H(src["path"]), src.get("id_prefix", "miss"), src.get("project", "tooling")
        if not os.path.exists(f):
            continue
        for line in open(f):
            m = re.match(r"^\| (\d+) \| ([^|]*)\|(.*)\|\s*([^|]*)\|\s*$", line)
            if not m:
                continue
            st = (m.group(4).strip().split() or ["?"])[0].strip("*`").lower()
            if st not in ("open", "workaround"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            put(db, item_id=f"miss:{prefix}-{m.group(1)}", source_type="miss", kind="tooling-gap", project=project,
                title=re.sub(r"\*\*|`", "", cells[2])[:200], detail=" | ".join(cells[3:])[:900], owner="owner",
                ts=cells[1], last_ts=cells[1], status="open", status_reason=f"status: {st}",
                links=[f + f" row {m.group(1)}"])


def open_sections(db):
    """Bullets under a "## Open" heading in a notes file."""
    for src in CFG["open_section_files"]:
        f, project, prefix = H(src["path"]), src["project"], src.get("id_prefix", "open")
        if not os.path.exists(f):
            continue
        sec = re.search(r"^## Open\n(.*?)(?=^## |\Z)", open(f).read(), re.S | re.M)
        if not sec:
            continue
        ts = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(f)))
        for i, b in enumerate(re.findall(r"^- (.+)$", sec.group(1), re.M)):
            title = re.sub(r"\*\*", "", b)
            put(db, item_id=f"review:{prefix}-{i}", source_type="review", kind="decision-pending",
                project=project, title=title[:200], detail=title[:900], owner="owner", ts=ts, last_ts=ts,
                status="open", status_reason=f"listed under Open in {src['path']}", links=[f])


def main():
    db = sqlite3.connect(str(WORK_DB), timeout=600)
    db.execute(SCHEMA)
    for fn in (prs, lanes, inbox, misses, open_sections):
        fn(db)
        db.commit()
    for r in db.execute("select source_type, kind, count(*) from items group by 1,2"):
        print(*r, file=sys.stderr)


if __name__ == "__main__":
    main()
