import os
import tempfile
import unittest
from unittest.mock import patch

from db import DB


class DatabaseBackendSelectionTests(unittest.TestCase):
    def test_postgres_sql_uses_psycopg_placeholders(self):
        db = DB.__new__(DB)
        db.backend = "postgres"
        self.assertEqual(
            db._adapt_sql("SELECT * FROM signals WHERE id=? AND status=?"),
            "SELECT * FROM signals WHERE id=%s AND status=%s",
        )

    def test_sqlite_sql_keeps_qmark_placeholders(self):
        db = DB.__new__(DB)
        db.backend = "sqlite"
        query = "SELECT * FROM signals WHERE id=?"
        self.assertEqual(db._adapt_sql(query), query)

    def test_invalid_database_url_fails_instead_of_using_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"DATABASE_URL": "mysql://invalid"}):
                with self.assertRaisesRegex(ValueError, "DATABASE_URL"):
                    DB(os.path.join(tmp, "signals.sqlite3"))


if __name__ == "__main__":
    unittest.main()
