import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


LIB = Path(__file__).parents[1] / "tools" / "unfinished" / "lib"
sys.path.insert(0, str(LIB))
import adjudicate
import render


class AdjudicatedDoneTests(unittest.TestCase):
    def test_fresh_model_done_stays_visible_with_original_reason(self):
        self.exercise(cached=False)

    def test_cached_model_done_stays_visible_with_original_reason(self):
        self.exercise(cached=True)

    def test_checked_completion_overrides_the_model_verdict(self):
        self.exercise(cached=True, checked=True)

    def exercise(self, cached, checked=False):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / "work.sqlite3"
            connection = __import__("sqlite3").connect(database)
            connection.executescript("""
                create table items (
                    item_id text, source_type text, kind text, project text, title text, detail text,
                    owner text, ts text, last_ts text, status_reason text, links text, quote text,
                    session_key text, seg_id text
                );
                create table prerank (item_id text, score real, bundle text);
                create table evidence (item_id text, prs text, snippets text);
                create table t3threads (session_key text, url text);
                create table dups (item_id text, survivor text);
            """)
            item_id = "s:unfinished-item"
            connection.execute(
                "insert into items values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (item_id, "session", "action", "sample", "Finish the task", "{}", "agent",
                 "2026-01-01", "2026-01-01", "", "[]", "source quote", None, None),
            )
            connection.execute("insert into prerank values (?,?,?)", (item_id, 1.0, None))
            connection.execute("insert into evidence values (?,?,?)", (item_id, "[]", "[]"))
            connection.commit()
            connection.close()

            model_result = {"results": [{
                "id": item_id,
                "verdict": "done",
                "verdict_reason": "The source text says the task is done.",
                "needs_owner": False,
                "next_action": "Confirm the task is finished",
                "why": "The ledger needs a current-state check.",
                "priority": 2,
                "effort": "minutes",
                "duplicate_of": "",
                "confidence": "high",
            }]}
            if cached:
                with __import__("sqlite3").connect(database) as connection:
                    connection.execute("create table adj (item_id text primary key, out text, model text)")
                    connection.execute("insert into adj values (?,?,?)",
                                       (item_id, json.dumps(model_result["results"][0]), "fixture"))
            else:
                with (
                    mock.patch.object(adjudicate, "WORK_DB", database),
                    mock.patch.object(adjudicate, "call", return_value=(model_result, 0.0)),
                    mock.patch.object(adjudicate, "claude_env", return_value={}),
                    mock.patch.object(sys, "argv", ["adjudicate.py"]),
                ):
                    adjudicate.main()

            with __import__("sqlite3").connect(database) as connection:
                stored = json.loads(connection.execute("select out from adj where item_id=?", (item_id,)).fetchone()[0])
                self.assertEqual(stored["verdict"], "done")
                self.assertEqual(stored["verdict_reason"], model_result["results"][0]["verdict_reason"])
                if checked:
                    connection.execute("create table resolved (item_id text primary key, out text)")
                    connection.execute("insert into resolved values (?,?)",
                                       (item_id, json.dumps({"status_now": "done", "evidence": "Commit verified"})))

            data_dir = root / "data"
            evidence_dir = data_dir / "evidence"
            state_dir = root / "state"
            evidence_dir.mkdir(parents=True)
            state_dir.mkdir(parents=True)
            with (
                mock.patch.object(render, "WORK_DB", database),
                mock.patch.object(render, "DATA", data_dir),
                mock.patch.object(render, "EVIDENCE", evidence_dir),
                mock.patch.object(render, "STATE", state_dir),
                mock.patch.object(render, "OUT", str(data_dir)),
                mock.patch.object(render, "EV", str(evidence_dir)),
                mock.patch.object(render, "DEC", str(data_dir / "decisions.jsonl")),
                mock.patch.object(render, "GEN", str(data_dir / ".generation")),
            ):
                render.main()

            ledger = (data_dir / "LEDGER.md").read_text(encoding="utf-8")
            saved = json.loads((data_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertIn(f"Likely done: glance and tick ({0 if checked else 1})", ledger)
            self.assertIn(item_id, ledger)
            self.assertEqual(saved["verdict"], "done" if checked else "likely-done")
            self.assertEqual(saved["verdict_reason"], "Commit verified" if checked else model_result["results"][0]["verdict_reason"])


if __name__ == "__main__":
    unittest.main()
