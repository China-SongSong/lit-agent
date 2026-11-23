"""溯源链路的行为约束测试。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from litagent.evaluation.calibration import build_evaluation_store, load_cases
from litagent.retrieve.trace import QuoteTracer
from litagent.schema import Document

FIXTURES=Path(__file__).resolve().parent/"fixtures"
CORPUS=FIXTURES/"calibration_corpus_v2.jsonl"
QUERIES=FIXTURES/"calibration_queries_v2.jsonl"


class TracePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls)->None:
        cls.temp=tempfile.TemporaryDirectory(prefix="litagent-tests-")
        cls.store,cls.settings=build_evaluation_store(CORPUS,Path(cls.temp.name))
        cls.cases={case.id:case for case in load_cases(QUERIES)}

    @classmethod
    def tearDownClass(cls)->None:
        cls.store.close_all()
        cls.temp.cleanup()

    def test_bm25_support_does_not_change_confidence_or_found(self)->None:
        tracer=QuoteTracer(self.store,self.settings)
        query=self.cases["q-en-partial-confirm"].query
        with patch.object(tracer,"_check_bm25_support",return_value=False):
            without,_=tracer.trace(query,allow_online=False,found_threshold=0.68)
        with patch.object(tracer,"_check_bm25_support",return_value=True):
            with_support,_=tracer.trace(query,allow_online=False,found_threshold=0.68)
        self.assertEqual(without.confidence,with_support.confidence)
        self.assertEqual(without.found,with_support.found)
        self.assertFalse(without.supporting_signals["bm25"])
        self.assertTrue(with_support.supporting_signals["bm25"])

    def test_offline_trace_never_calls_online_provider(self)->None:
        tracer=QuoteTracer(self.store,self.settings)
        query=self.cases["q-cn-paraphrase"].query
        with patch.object(
            tracer,"_online_search_one",side_effect=AssertionError("offline called online")
        ) as mocked:
            result,report=tracer.trace(query,allow_online=False)
        mocked.assert_not_called()
        self.assertFalse(report.online_queried)
        self.assertFalse(result.found)

    def test_local_found_has_fingerprint_decision_basis(self)->None:
        tracer=QuoteTracer(self.store,self.settings)
        query=self.cases["q-en-exact"].query
        result,_=tracer.trace(query,allow_online=False)
        self.assertTrue(result.found)
        self.assertEqual(result.decision_basis,"local_fingerprint")
        self.assertIn("phrase",result.hit.sources)

    def test_short_query_whitespace_variant_uses_normalized_fast_path(self)->None:
        tracer=QuoteTracer(self.store,self.settings)
        query="Citation   tracing\nsystems\tmust separate"
        with patch(
            "litagent.index.phrase._best_contiguous_match",
            side_effect=AssertionError("unexpected slow fallback"),
        ):
            result,_=tracer.trace(query,allow_online=False)
        self.assertTrue(result.found)
        self.assertEqual(result.hit.doc_id,"eval:citation-english")
        self.assertEqual(result.level,"local_fuzzy")

    def test_chinese_short_query_ignores_inserted_whitespace(self)->None:
        tracer=QuoteTracer(self.store,self.settings)
        query="过 度\n依 赖\t邻 居"
        with patch(
            "litagent.index.phrase._best_contiguous_match",
            side_effect=AssertionError("unexpected slow fallback"),
        ):
            result,_=tracer.trace(query,allow_online=False)
        self.assertTrue(result.found)
        self.assertEqual(result.hit.doc_id,"eval:gnn-cold-start")
        self.assertEqual(result.level,"local_exact")

    def test_truncated_short_query_uses_complete_prefix_prefilter(self)->None:
        tracer=QuoteTracer(self.store,self.settings)
        query="Semantic similarity is useful for find"
        with patch.object(
            self.store,
            "postings_for_terms",
            side_effect=AssertionError("truncated query used full postings fallback"),
        ):
            hits=tracer.phrase.search(query,top_k=5)
        self.assertTrue(hits)
        self.assertTrue(hits[0].chunk_id)
        self.assertEqual(hits[0].query_token_end,5)
        self.assertAlmostEqual(hits[0].score,5/6,places=4)

    def test_online_candidates_are_stamped_as_non_local(self)->None:
        tracer=QuoteTracer(self.store,self.settings)
        query=self.cases["q-cn-paraphrase"].query
        document=Document(
            id="doi:10.9000/online",
            doi="10.9000/online",
            title="逐字重合语义相近网页搜索结果原文来源",
            source="openalex",
            url="https://example.org/online",
        )
        with patch.object(
            tracer,
            "_online_search_one",
            return_value=("openalex",[document],""),
        ), patch.object(
            tracer,
            "_opencli_search",
            return_value=("opencli",[],""),
        ):
            result,report=tracer.trace(query,allow_online=True)
        self.assertTrue(report.online_queried)
        self.assertFalse(result.found)
        self.assertEqual(result.decision_basis,"none")
        self.assertTrue(result.online_candidates)
        for candidate in result.online_candidates:
            self.assertFalse(candidate["is_local_evidence"])
            self.assertEqual(candidate["candidate_kind"],"online_candidate")
        self.assertEqual(
            result.online_candidates[0]["url"],
            "https://example.org/online",
        )

    def test_opencli_candidates_use_browser_supplement_tier(self)->None:
        tracer=QuoteTracer(self.store,self.settings)
        query=self.cases["q-cn-paraphrase"].query
        document=Document(
            id="opencli:wanfang:test",
            title="逐字重合语义相近网页搜索结果原文来源",
            source="opencli:wanfang",
            url="https://example.org/opencli",
        )
        with patch.object(
            tracer,"_online_search_one",return_value=("openalex",[],"")
        ), patch.object(
            tracer,"_opencli_search",return_value=("opencli",[document],"")
        ):
            result,report=tracer.trace(query,allow_online=True)
        self.assertFalse(result.found)
        self.assertTrue(report.online_queried)
        self.assertEqual(result.online_candidates[0]["source_tier"],"browser_supplement")
        self.assertFalse(result.online_candidates[0]["is_local_evidence"])

    def test_opencli_failure_does_not_block_api_candidates(self)->None:
        tracer=QuoteTracer(self.store,self.settings)
        query=self.cases["q-cn-paraphrase"].query
        document=Document(
            id="doi:10.9000/api",
            doi="10.9000/api",
            title="逐字重合语义相近网页搜索结果原文来源",
            source="openalex",
            url="https://example.org/api",
        )
        with patch.object(
            tracer,"_online_search_one",return_value=("openalex",[document],"")
        ), patch.object(
            tracer,"_opencli_search",return_value=(
                "opencli",[],"google-scholar: timeout"
            )
        ):
            result,report=tracer.trace(query,allow_online=True)
        self.assertTrue(result.online_candidates)
        self.assertEqual(result.online_candidates[0]["source_tier"],"primary_api")
        self.assertIn("google-scholar",report.online_error)

    def test_chinese_query_filters_foreign_api_noise(self)->None:
        tracer=QuoteTracer(self.store,self.settings)
        query=self.cases["q-cn-paraphrase"].query
        chinese=Document(
            id="doi:10.9000/chinese",
            doi="10.9000/chinese",
            title="逐字重合语义相近网页搜索结果原文来源",
            language="zh",
            source="openalex",
            url="https://example.org/chinese",
        )
        foreign=Document(
            id="doi:10.9000/foreign",
            doi="10.9000/foreign",
            title="Unrelated foreign paper",
            language="en",
            source="crossref",
        )
        with patch.object(
            tracer,"_online_search_one",
            side_effect=lambda name,keywords,topic:(
                name,[chinese,foreign],""
            ),
        ), patch.object(
            tracer,"_opencli_search",
            return_value=("opencli",[],""),
        ):
            result,report=tracer.trace(query,allow_online=True)
        self.assertEqual(
            [item["title"] for item in result.online_candidates],
            ["逐字重合语义相近网页搜索结果原文来源"],
        )
        self.assertEqual(report.online_filtered_cross_language,1)

    def test_chinese_route_skips_crossref_rate_limit_noise(self)->None:
        self.assertEqual(QuoteTracer._online_sources_for("zh"),("openalex",))

    def test_explicit_doi_hint_is_kept_and_exposes_link(self)->None:
        tracer=QuoteTracer(self.store,self.settings)
        query=self.cases["q-cn-paraphrase"].query
        hinted=Document(
            id="doi:10.9000/hinted",
            doi="10.9000/hinted",
            title="Explicitly hinted paper",
            language="en",
            source="crossref",
            url="https://doi.org/10.9000/hinted",
        )
        with patch.object(
            tracer,"_online_search_one",return_value=("openalex",[],"")
        ), patch.object(
            tracer,"_opencli_search",return_value=("opencli",[],"")
        ), patch.object(
            tracer,"_hint_search",return_value=("source-hint",[hinted],"")
        ):
            result,_=tracer.trace(
                query,allow_online=True,
                source_hint="https://doi.org/10.9000/hinted",
            )
        self.assertEqual(len(result.online_candidates),1)
        self.assertEqual(
            result.online_candidates[0]["url"],
            "https://doi.org/10.9000/hinted",
        )


if __name__=="__main__":
    unittest.main()
