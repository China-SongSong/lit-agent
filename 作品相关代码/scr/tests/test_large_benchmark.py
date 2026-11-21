"""大规模固定基准生成器的结构与冻结字段测试。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from litagent.evaluation.large_benchmark import generate_benchmark


class LargeBenchmarkTests(unittest.TestCase):
    def test_generator_covers_all_topics_and_content_levels(self)->None:
        with tempfile.TemporaryDirectory(prefix="litagent-large-fixture-") as temp:
            output=Path(temp)/"benchmark"
            manifest=generate_benchmark(output,document_count=500,seed=7)
            self.assertEqual(manifest["documents"],500)
            self.assertEqual(manifest["topics"],50)
            self.assertTrue(manifest["frozen"])
            counts=manifest["content_counts"]
            self.assertEqual(
                counts["fulltext"]+counts["abstract"]+counts["title_only"],
                500,
            )
            self.assertGreater(len(manifest["corpus_sha256"]),32)
            first=json.loads((output/"corpus.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()[0])
            self.assertEqual(first["source"],"benchmark")


if __name__=="__main__":
    unittest.main()
