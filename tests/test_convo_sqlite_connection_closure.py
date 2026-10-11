import importlib.util
import sqlite3
import unittest
from pathlib import Path
from unittest import mock


SOURCE = Path(__file__).with_name("test_convo.py")
SPEC = importlib.util.spec_from_file_location("convo_test_suite", SOURCE)
convo_test_suite = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(convo_test_suite)


class TrackingConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.was_closed = False

    def close(self):
        self.was_closed = True
        super().close()


class SQLiteConnectionClosureTests(unittest.TestCase):
    def test_schema_upgrade_test_closes_every_connection(self):
        real_connect = sqlite3.connect
        connections = []

        def tracking_connect(*args, **kwargs):
            kwargs.setdefault("factory", TrackingConnection)
            connection = real_connect(*args, **kwargs)
            connections.append(connection)
            return connection

        case = convo_test_suite.LedgerTests(
            "test_future_schema_version_is_not_downgraded_and_status_fails_cleanly"
        )
        result = unittest.TestResult()
        with mock.patch.object(sqlite3, "connect", side_effect=tracking_connect):
            case.run(result)

        self.assertEqual(result.errors, [])
        self.assertEqual(result.failures, [])
        self.assertGreaterEqual(len(connections), 3)
        self.assertTrue(all(connection.was_closed for connection in connections))


if __name__ == "__main__":
    unittest.main()
