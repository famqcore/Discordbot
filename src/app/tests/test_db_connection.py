import importlib
import os
import sqlite3
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
