import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


LIB = Path(__file__).parents[1] / "tools" / "unfinished" / "lib"
sys.path.insert(0, str(LIB))
import cluster_top


class ClusterTopFailureTests(unittest.TestCase):
    def test_model_error_preserves_existing_clusters_and_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "work.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript("""
                create table items (item_id text, project text);
                create table resolved (item_id text, out text);
                create table top_clusters (item_id text primary key, survivor text, merged_action text);
                insert into items values ('s:item', 'sample');
            """)
            connection.execute(
                "insert into resolved values (?, ?)",
                ("s:item", json.dumps({"status_now": "open", "next_action": "finish", "evidence": ""})),
            )
            connection.execute("insert into top_clusters values (?,?,?)", ("s:old", "s:old", "old merge"))
            connection.commit()
            connection.close()
            failed_output = json.dumps({"is_error": True, "result": "model unavailable"})

            with (
                mock.patch.object(cluster_top, "WORK_DB", database),
                mock.patch.object(cluster_top, "STATE", Path(temp)),
                mock.patch.object(cluster_top, "claude_env", return_value={}),
                mock.patch.object(
                    cluster_top.subprocess,
                    "run",
                    return_value=__import__("subprocess").CompletedProcess(
                        ["claude"], 1, stdout=failed_output, stderr="unavailable",
                    ),
                ),
                self.assertRaises(SystemExit),
            ):
                cluster_top.main()

            connection = sqlite3.connect(database)
            clusters = connection.execute("select * from top_clusters").fetchall()
            connection.close()
            self.assertEqual(clusters, [("s:old", "s:old", "old merge")])


if __name__ == "__main__":
    unittest.main()
