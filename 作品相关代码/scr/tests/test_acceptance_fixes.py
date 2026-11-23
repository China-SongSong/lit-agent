"""上线验收与黑盒反馈的回归测试。"""
from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from litagent.config import Settings
from litagent.index.store import Store
from litagent.index.vector import VectorHit
from litagent.logging_setup import setup_logging
from litagent.presentation import readable_text_html
from litagent.retrieve.trace import (
    QuoteTracer,
    parse_source_hint,
    redact_source_hint,
)
from litagent.schema import Chunk, Document


class PresentationTests(unittest.TestCase):
    def test_fulltext_reader_has_dark_text_and_safe_markup(self)->None:
        rendered=readable_text_html(
            "第一行\n第二行\n\n<script>alert(1)</script>"
        )
        self.assertIn("color:#172033",rendered)
        self.assertIn("line-height:1.9",rendered)
        self.assertIn("overflow-y:auto",rendered)
        self.assertIn("第一行第二行",rendered)
        self.assertNotIn("<script>",rendered)
        self.assertIn("&lt;script&gt;",rendered)

    def test_cnki_source_hint_is_redacted(self)->None:
        hint=(
            "https://kns.cnki.net/reader/flowpdf?"
            "invoice=secret&nonce=token&filename=ZGFX202605001"
        )
        self.assertEqual(
            parse_source_hint(hint),
            ("cnki_filename","ZGFX202605001"),
        )
        safe=redact_source_hint(hint)
        self.assertEqual(safe,"CNKI filename=ZGFX202605001")
        self.assertNotIn("secret",safe)
        self.assertNotIn("nonce",safe)


class TraceAcceptanceTests(unittest.TestCase):
    def setUp(self)->None:
        self.temp=tempfile.TemporaryDirectory(prefix="litagent-acceptance-")
        self.root=Path(self.temp.name)
        self.store=Store(self.root/"index.db")
        self.store.init_schema()

    def tearDown(self)->None:
        self.store.close_all()
        self.temp.cleanup()

    def test_overlong_trace_is_rejected_before_index_work(self)->None:
        tracer=QuoteTracer(
            self.store,
            Settings(data_dir=self.root,trace_max_chars=1000),
        )
        with patch.object(
            tracer.phrase,
            "search",
            side_effect=AssertionError("index should not run"),
        ):
            result,report=tracer.trace("法"*1001,allow_online=False)
        self.assertEqual(result.level,"input_too_long")
        self.assertLessEqual(len(result.query),200)
        self.assertEqual(report.quote_chars,1001)

    def test_chinese_query_does_not_promote_foreign_semantic_hit(self)->None:
        document=Document(
            id="doc:foreign",
            title="Foreign semantic candidate",
            language="en",
            source="synthetic",
        )
        self.store.upsert_document(document)
        text="This is a foreign-language semantic candidate."
        chunk=Chunk(
            id=Chunk.make_id(document.id,0,text),
            doc_id=document.id,
            text=text,
            token_count=7,
            content_hash="fixture",
        )
        self.store.set_fulltext(document.id,text)
        self.store.replace_chunks(document.id,[chunk])
        tracer=QuoteTracer(
            self.store,
            Settings(data_dir=self.root,vector_enabled=True),
        )
        with patch.object(tracer.phrase,"search",return_value=[]), patch.object(
            tracer,
            "_vector_search",
            return_value=[VectorHit(chunk.id,document.id,0.31,text)],
        ):
            result,report=tracer.trace(
                "社会权条款的立法义务如何转化为个人权利",
                allow_online=False,
            )
        self.assertEqual(result.level,"local_semantic")
        self.assertFalse(result.found)
        self.assertIsNone(result.hit)
        self.assertEqual(report.vector_cross_language,1)
        self.assertEqual(result.candidates[0].doc_id,document.id)

    def test_cnki_filename_prioritizes_matching_online_candidate(self)->None:
        tracer=QuoteTracer(
            self.store,
            Settings(data_dir=self.root,vector_enabled=False),
        )
        matching=Document(
            id="opencli:cnki:matching",
            title="社会权的义务构造",
            language="zh",
            source="opencli:cnki",
            url=(
                "https://kns.cnki.net/kcms2/article/abstract"
                "?v=ZGFX202605001"
            ),
        )
        unrelated=Document(
            id="opencli:cnki:unrelated",
            title="社会权研究综述",
            language="zh",
            source="opencli:cnki",
            url="https://example.org/unrelated",
        )
        with patch.object(
            tracer,"_online_search_one",return_value=("openalex",[],"")
        ), patch.object(
            tracer,
            "_opencli_search",
            return_value=("opencli",[unrelated,matching],""),
        ):
            result,report=tracer.trace(
                "社会权条款的立法义务与个人主张",
                allow_online=True,
                source_hint=(
                    "https://kns.cnki.net/reader/flowpdf?"
                    "invoice=secret&nonce=token&filename=ZGFX202605001"
                ),
            )
        self.assertEqual(result.online_candidates[0]["id"],matching.id)
        self.assertNotIn("未包含该标识",report.online_error)


class LoggingTests(unittest.TestCase):
    def test_logging_creates_rotating_file_without_duplicate_handlers(self)->None:
        with tempfile.TemporaryDirectory(prefix="litagent-log-") as temp:
            settings=Settings(data_dir=Path(temp))
            path=setup_logging(settings,component="test")
            setup_logging(settings,component="test")
            logging.getLogger("litagent.test").info("probe")
            for handler in logging.getLogger("litagent").handlers:
                handler.flush()
            self.assertIsNotNone(path)
            self.assertTrue(path.is_file())
            self.assertIn("component=test",path.read_text(encoding="utf-8"))
            file_handlers=[
                handler
                for handler in logging.getLogger("litagent").handlers
                if getattr(handler,"_litagent_file_handler",False)
            ]
            self.assertEqual(len(file_handlers),1)
            for handler in file_handlers:
                logging.getLogger("litagent").removeHandler(handler)
                handler.close()


if __name__=="__main__":
    unittest.main()
