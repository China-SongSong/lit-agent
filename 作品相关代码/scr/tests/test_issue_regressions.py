"""Issues.md 中可复现问题的回归测试。"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from litagent.clean.chunker import chunk_text
from litagent.config import Settings, build_settings
from litagent.evaluation.corpus_build import (
    logical_database_sha256,
    logical_database_table_sha256,
)
from litagent.fetch.local_pdf import extract_doi_from_text, guess_title_from_filename


class ConfigurationBoundsTests(unittest.TestCase):
    def test_valid_nondefault_values_are_preserved(self)->None:
        with patch.dict(
            "os.environ",
            {
                "LIT_MAX_CHUNK_CHARS":"400",
                "LIT_CHUNK_OVERLAP_CHARS":"100",
                "LIT_FINGERPRINT_N":"5",
                "LIT_BM25_B":"0.9",
                "LIT_VECTOR_DIM":"256",
            },
            clear=False,
        ):
            settings=build_settings()
        self.assertEqual(settings.max_chunk_chars,400)
        self.assertEqual(settings.chunk_overlap_chars,100)
        self.assertEqual(settings.fingerprint_n,5)
        self.assertEqual(settings.bm25_b,0.9)
        self.assertEqual(settings.vector_dim,256)

    def test_invalid_chunk_values_fall_back_to_defaults(self)->None:
        with patch.dict(
            "os.environ",
            {
                "LIT_MAX_CHUNK_CHARS":"0",
                "LIT_CHUNK_OVERLAP_CHARS":"-1",
                "LIT_OPENCLI_TIMEOUT":"0",
                "LIT_VECTOR_DIM":"0",
                "LIT_VECTOR_TOP_K":"0",
            },
            clear=False,
        ):
            settings=build_settings()
        self.assertEqual(settings.max_chunk_chars,1800)
        self.assertEqual(settings.chunk_overlap_chars,200)
        self.assertEqual(settings.opencli_timeout,12.0)
        self.assertEqual(settings.vector_dim,384)
        self.assertEqual(settings.vector_top_k,10)

    def test_direct_settings_construction_is_also_bounded(self)->None:
        settings=Settings(
            max_chunk_chars=0,
            chunk_overlap_chars=-3,
            fingerprint_n=2,
            default_top_k=0,
        )
        self.assertEqual(settings.max_chunk_chars,1800)
        self.assertEqual(settings.chunk_overlap_chars,200)
        self.assertEqual(settings.fingerprint_n,7)
        self.assertEqual(settings.default_top_k,10)


class ChunkerSafetyTests(unittest.TestCase):
    def test_zero_and_negative_limits_still_make_progress(self)->None:
        text="没有标点的超长段落"*40
        for limit in (0,-1):
            chunks=chunk_text("doi:chunk-safety",text,max_chars=limit)
            self.assertTrue(chunks)
            self.assertEqual("".join(chunk.text for chunk in chunks),text)


class CorpusHashScopeTests(unittest.TestCase):
    def test_summary_and_meta_changes_do_not_change_corpus_hash(self)->None:
        with tempfile.TemporaryDirectory(prefix="litagent-hash-") as temp:
            database=Path(temp)/"index.db"
            conn=sqlite3.connect(database)
            try:
                conn.execute(
                    "CREATE TABLE documents("
                    "id TEXT PRIMARY KEY,title TEXT,summarized_at TEXT)"
                )
                conn.execute("CREATE TABLE summaries(doc_id TEXT PRIMARY KEY,tldr TEXT)")
                conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
                conn.execute(
                    "INSERT INTO documents VALUES("
                    "'doc:1','Stable title','2026-01-01T00:00:00+00:00')"
                )
                conn.execute("INSERT INTO summaries VALUES('doc:1','before')")
                conn.execute("INSERT INTO meta VALUES('schema','1')")
                conn.commit()
            finally:
                conn.close()

            before=logical_database_sha256(database)
            self.assertEqual(
                list(logical_database_table_sha256(database)),
                ["documents"],
            )
            conn=sqlite3.connect(database)
            try:
                conn.execute("UPDATE summaries SET tldr='after'")
                conn.execute("UPDATE meta SET value='2'")
                conn.execute(
                    "UPDATE documents SET summarized_at='2026-11-18T00:00:00+00:00'"
                )
                conn.commit()
            finally:
                conn.close()
            self.assertEqual(logical_database_sha256(database),before)

            conn=sqlite3.connect(database)
            try:
                conn.execute("UPDATE documents SET title='Changed title'")
                conn.commit()
            finally:
                conn.close()
            self.assertNotEqual(logical_database_sha256(database),before)


class CnkPdfMetadataRegressionTests(unittest.TestCase):
    """Issue #5：CNKI 中文 PDF 占位符元数据 + DOI 误抓导致去重误杀。"""

    def test_doi_prefix_fragment_is_rejected(self)->None:
        # CNKI 导出 PDF 首页常见的 DOI 前缀片段，后缀过短，必须判为无效。
        self.assertIsNone(extract_doi_from_text("10.3963/j"))
        self.assertIsNone(extract_doi_from_text("doi: 10.3963/j 中文摘要"))
        # 完整 DOI 仍应正常提取。
        self.assertEqual(
            extract_doi_from_text("10.1007/s11214-021-00831-w"),
            "10.1007/s11214-021-00831-w",
        )

    def test_doi_with_empty_or_punct_only_suffix_is_rejected(self)->None:
        self.assertIsNone(extract_doi_from_text("10.3963/"))
        self.assertIsNone(extract_doi_from_text("10.3963/j."))

    def test_filename_fallback_for_cnki_placeholder_title(self)->None:
        # 标题为中文占位符时，应回退到文件名。
        path = Path("基于LIBS技术的银合金分类及定量分析研究.pdf")
        self.assertEqual(
            guess_title_from_filename(path),
            "基于LIBS技术的银合金分类及定量分析研究",
        )


if __name__=="__main__":
    unittest.main()
