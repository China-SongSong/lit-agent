"""向量召回与证据边界测试。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from litagent.evaluation.calibration import build_evaluation_store
from litagent.index.builder import IndexBuilder
from litagent.index.vector import NEURAL_MODEL,cosine
from litagent.retrieve.trace import QuoteTracer

FIXTURES=Path(__file__).resolve().parent/"fixtures"
CORPUS=FIXTURES/"calibration_corpus_v2.jsonl"


class VectorRecallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls)->None:
        cls.temp=tempfile.TemporaryDirectory(prefix="litagent-vector-")
        cls.store,cls.settings=build_evaluation_store(
            CORPUS,Path(cls.temp.name)
        )
        cls.builder=IndexBuilder(cls.store,cls.settings)
        cls.report=cls.builder.build_vectors(
            rebuild=True
        )

    @classmethod
    def tearDownClass(cls)->None:
        cls.store.close_all()
        cls.temp.cleanup()

    def test_neural_vectors_are_normalized_and_capture_semantics(self)->None:
        query,related,unrelated,translated=self.builder.embedding_encoder.encode([
            "引文溯源需要区分原文证据和外部候选",
            "论文出处核验必须把文本证据与网页线索分开",
            "图神经网络用于推荐系统冷启动",
            "Citation tracing must separate textual evidence from external candidates",
        ])
        self.assertAlmostEqual(cosine(query,query),1.0,places=4)
        self.assertGreater(
            cosine(query,related),
            cosine(query,unrelated),
        )
        self.assertGreater(
            cosine(query,translated),
            cosine(query,unrelated),
        )

    def test_index_builds_all_chunk_vectors(self)->None:
        self.assertEqual(
            self.report.vectors_written,
            self.store.count_chunks(),
        )
        self.assertEqual(
            self.store.count_chunk_vectors(),
            self.store.count_chunks(),
        )
        self.assertEqual(self.builder.vector_model,NEURAL_MODEL)

    def test_semantic_recall_selects_document_without_claiming_found(self)->None:
        tracer=QuoteTracer(self.store,self.settings)
        result,report=tracer.trace(
            "引文溯源证据分层原文候选",allow_online=False
        )
        self.assertFalse(result.found)
        self.assertEqual(result.level,"local_semantic")
        self.assertEqual(result.decision_basis,"none")
        self.assertIsNotNone(result.hit)
        self.assertEqual(result.hit.doc_id,"eval:citation-contract")
        self.assertEqual(result.hit.sources,["vector"])
        self.assertGreater(report.vector_candidates,0)
        self.assertGreater(
            result.supporting_signals["vector_similarity"],0
        )

    def test_exact_hit_skips_vector_route(self)->None:
        tracer=QuoteTracer(self.store,self.settings)
        quote=(
            "Citation tracing systems must separate exact textual evidence "
            "from semantic similarity."
        )
        result,report=tracer.trace(quote,allow_online=False)
        self.assertTrue(result.found)
        self.assertEqual(report.vector_candidates,0)


if __name__=="__main__":
    unittest.main()
