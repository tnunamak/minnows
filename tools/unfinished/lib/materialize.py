"""Turn extracted seg_items into `items` rows (source_type='session').

Checks each quote against the source text (whitespace-normalized; '...' splits
allowed) and drops items whose quote is not found, plus trivial weights and the
standing-rule / recap-request kinds.
"""
import hashlib, json, os, re, sqlite3, sys
from uf_config import WORK_DB



def norm(s):
    return re.sub(r"\s+", " ", (s or "").replace("’", "'").replace("—", "-")).strip().lower()


def quote_ok(quote, src):
    """Require each normalized quote span to occur in the source text."""
    spans = [norm(part) for part in (quote or "").split("...") if norm(part)]
    if not spans:
        return False
    source = norm(src)
    cursor = 0
    for span in spans:
        position = source.find(span, cursor)
        if position < 0:
            return False
        cursor = position + len(span)
    return True


def main():
    db = sqlite3.connect(str(WORK_DB), timeout=600)
    db.execute("delete from items where source_type='session'")
    stats = {"items": 0, "kept": 0, "bad_quote": 0, "trivial": 0}
    for seg_id, items_json in db.execute("select seg_id, items from seg_items").fetchall():
        seg = db.execute("""select kind, harness, session_key, source_path, project, ts, text, context
                            from segments where seg_id=?""", (seg_id,)).fetchone()
        if not seg:
            continue
        kind, harness, sk, path, project, ts, text, ctx = seg
        why_sel = (db.execute("select why from cands where seg_id=?", (seg_id,)).fetchone() or [""])[0]
        for n, it in enumerate(json.loads(items_json)):
            stats["items"] += 1
            if it.get("weight") == "trivial":
                stats["trivial"] += 1
                continue
            ok = quote_ok(it.get("quote"), (text or "") + "\n" + (ctx or ""))
            if not ok:
                stats["bad_quote"] += 1
                continue
            iid = "s:" + hashlib.sha256(f"{seg_id}|{n}|{it.get('action')}".encode()).hexdigest()[:12]
            repo = it.get("repo") or os.path.basename((project or "").rstrip("/"))
            db.execute("""insert or replace into items (item_id, source_type, kind, project, title, detail, owner, ts,
                          last_ts, status, status_reason, evidence, links, raw, session_key, seg_id, quote)
                          values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (iid, "session", it.get("kind"), repo, it.get("action"),
                        json.dumps({"object": it.get("object"), "why": it.get("why"), "weight": it.get("weight"),
                                    "trigger": why_sel, "seg_kind": kind}),
                        it.get("owner"), ts, ts, "open", "", None,
                        json.dumps([f"convo show {sk.split(':', 1)[1]}", path]), json.dumps(it), sk, seg_id,
                        it.get("quote")))
            stats["kept"] += 1
    db.commit()
    print(stats, file=sys.stderr)


if __name__ == "__main__":
    main()
