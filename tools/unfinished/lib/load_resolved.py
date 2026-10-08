"""Load agentic resolver / hand-verification outputs (JSON lines) into table
`resolved`. Later files win for the same item id.

  load_resolved.py FILE.jsonl [FILE.jsonl ...]
"""
import json, os, sqlite3, sys
from uf_config import WORK_DB



def main(paths):
    db = sqlite3.connect(str(WORK_DB), timeout=600)
    db.execute("create table if not exists resolved (item_id text primary key, out text, src text)")
    n = 0
    for p in paths:
        for line in open(p):
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if o.get("id"):
                db.execute("insert or replace into resolved values (?,?,?)", (o["id"], json.dumps(o), os.path.basename(p)))
                n += 1
    db.commit()
    print(f"loaded {n} resolutions", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
