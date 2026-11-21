"""旧库 chunk_text 规范化列迁移测试。"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from litagent.index.store import Store


class StoreMigrationTests(unittest.TestCase):
    def test_normalized_text_column_is_added_and_backfilled(self)->None:
        with tempfile.TemporaryDirectory(prefix="litagent-migration-") as temp:
            db=Path(temp)/"index.db"
            conn=sqlite3.connect(str(db))
            conn.execute(
                "CREATE TABLE chunk_text (chunk_id TEXT PRIMARY KEY, text TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO chunk_text(chunk_id,text) VALUES(?,?)",
                ("chunk:1","Citation   tracing\nsystems"),
            )
            conn.commit()
            conn.close()

            store=Store(db)
            try:
                store.init_schema()
                row=store.connect().execute(
                    "SELECT normalized_text FROM chunk_text WHERE chunk_id='chunk:1'"
                ).fetchone()
                self.assertEqual(row["normalized_text"],"citation\x1ftracing\x1fsystems")
                self.assertEqual(store.get_meta("schema_version"),"5")
                self.assertIn("documents_fulltext",store.table_names())
                store.connect().execute(
                    "SELECT doc_id,text FROM documents_fulltext LIMIT 1"
                ).fetchall()
            finally:
                store.close_all()


if __name__=="__main__":
    unittest.main()
