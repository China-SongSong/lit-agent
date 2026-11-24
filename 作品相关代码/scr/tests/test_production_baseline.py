"""生产语料冻结基线必须与当前书目数据一致。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import unittest
from pathlib import Path

from litagent.evaluation.corpus_build import (
    logical_database_sha256,
    logical_database_table_sha256,
)

ROOT=Path(__file__).resolve().parents[1]
BASELINE=Path(__file__).resolve().parent/"fixtures"/"production_baseline_v4.json"


def _sha256(path:Path)->str:
    digest=hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda:handle.read(1024*1024),b""):
            digest.update(block)
    return digest.hexdigest()


@unittest.skipUnless((ROOT/"data"/"index.db").is_file(),"生产索引不存在")
class ProductionBaselineTests(unittest.TestCase):
    def test_frozen_baseline_matches_current_corpus(self)->None:
        manifest=json.loads(BASELINE.read_text(encoding="utf-8"))
        database=ROOT/manifest["database"]
        queries=Path(__file__).resolve().parent/"fixtures"/manifest["queries"]

        self.assertEqual(
            logical_database_sha256(database),
            manifest["logical_sha256"],
        )
        self.assertEqual(
            logical_database_table_sha256(database),
            manifest["table_sha256"],
        )
        self.assertEqual(len(_sha256(database)),64)
        self.assertEqual(_sha256(queries),manifest["queries_sha256"])

        conn=sqlite3.connect(database.resolve().as_uri()+"?mode=ro",uri=True)
        try:
            counts={
                "documents":int(conn.execute(
                    "SELECT COUNT(*) FROM documents"
                ).fetchone()[0]),
                "documents_with_fulltext":int(conn.execute(
                    "SELECT COUNT(*) FROM documents WHERE has_fulltext=1"
                ).fetchone()[0]),
                "chunks":int(conn.execute(
                    "SELECT COUNT(*) FROM chunks"
                ).fetchone()[0]),
                "postings":int(conn.execute(
                    "SELECT COUNT(*) FROM postings"
                ).fetchone()[0]),
                "fingerprints":int(conn.execute(
                    "SELECT COUNT(*) FROM fingerprints"
                ).fetchone()[0]),
                "summaries":int(conn.execute(
                    "SELECT COUNT(*) FROM summaries"
                ).fetchone()[0]),
            }
        finally:
            conn.close()
        corpus_keys=(
            "documents","documents_with_fulltext","chunks",
            "postings","fingerprints",
        )
        self.assertEqual(
            {key:counts[key] for key in corpus_keys},
            {key:manifest["counts"][key] for key in corpus_keys},
        )


if __name__=="__main__":
    unittest.main()
