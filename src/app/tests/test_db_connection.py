import asyncio
import importlib
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import TimeoutError as FuturesTimeoutError
from unittest.mock import MagicMock, patch

import config
import database.db as db_module


class TestGetDb(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp.close()
        self.original_db_path = config.DB_PATH
        config.DB_PATH = self.temp.name
        importlib.reload(db_module)

    def tearDown(self):
        config.DB_PATH = self.original_db_path
        try:
            os.unlink(self.temp.name)
        except OSError:
            pass

    def test_get_db_returns_connection(self):
        conn = db_module.get_db()
        self.assertIsInstance(conn, sqlite3.Connection)
        conn.close()

    def test_get_db_row_factory(self):
        conn = db_module.get_db()
        self.assertEqual(conn.row_factory, sqlite3.Row)
        conn.close()

    def test_get_db_creates_file(self):
        self.assertTrue(os.path.exists(self.temp.name))

    def test_connection_can_execute(self):
        conn = db_module.get_db()
        c = conn.cursor()
        c.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)")
        c.execute("INSERT INTO test_table (name) VALUES (?)", ("test",))
        conn.commit()
        c.execute("SELECT * FROM test_table")
        row = c.fetchone()
        self.assertEqual(row["name"], "test")
        conn.close()

    def test_multiple_connections(self):
        conn1 = db_module.get_db()
        conn2 = db_module.get_db()
        self.assertIsInstance(conn1, sqlite3.Connection)
        self.assertIsInstance(conn2, sqlite3.Connection)
        conn1.close()
        conn2.close()


class TestDatabaseClose(unittest.TestCase):
    def test_close_handles_worker_timeout_and_reports_it(self):
        database = db_module.Database(":memory:")
        database._executor.shutdown(wait=True)
        executor = MagicMock()
        executor.submit.return_value.result.side_effect = FuturesTimeoutError()
        database._executor = executor

        with patch.object(db_module, "logger") as mock_logger:
            database.close()

        mock_logger.warning.assert_called_once_with("database.close outcome=timeout timeout_seconds=10")
        executor.shutdown.assert_called_once_with(wait=True)


class TestAsyncDatabaseGateway(unittest.IsolatedAsyncioTestCase):
    """Контракт шлюза: медленная SQLite-операция не останавливает bot loop."""

    def setUp(self):
        self.temp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp.close()
        self.gateway = db_module.Database(self.temp.name)

    def tearDown(self):
        self.gateway.close()
        try:
            os.unlink(self.temp.name)
        except OSError:
            pass

    async def test_arun_yields_to_event_loop_while_database_is_busy(self):
        operation_started = threading.Event()

        def slow_operation(_connection):
            operation_started.set()
            time.sleep(0.1)

        database_task = asyncio.create_task(self.gateway.arun(slow_operation))
        await asyncio.to_thread(operation_started.wait)

        loop_progressed = asyncio.Event()
        asyncio.get_running_loop().call_soon(loop_progressed.set)
        await asyncio.wait_for(loop_progressed.wait(), timeout=0.03)
        await database_task


if __name__ == "__main__":
    unittest.main()
