"""评测语料和阈值校准的最小回归测试。"""
from __future__ import annotations

import unittest
from pathlib import Path

from litagent.evaluation.calibration import DEFAULT_THRESHOLDS,calibrate,format_markdown

FIXTURES=Path(__file__).resolve().parent/"fixtures"


class CalibrationTests(unittest.TestCase):
    def test_calibration_report_has_thresholds_and_splits(self)->None:
        report=calibrate(
            FIXTURES/"calibration_corpus_v2.jsonl",
            FIXTURES/"calibration_queries_v2.jsonl",
            thresholds=DEFAULT_THRESHOLDS,
        )
        self.assertEqual(report.cases,14)
        self.assertEqual(len(report.thresholds),len(DEFAULT_THRESHOLDS))
        self.assertIn("calibration",report.by_split)
        self.assertIn("test",report.by_split)
        self.assertEqual(report.candidate_threshold,0.68)
        self.assertEqual(report.runtime_default_threshold,0.72)
        self.assertEqual(
            report.adoption_status,
            "not_adopted_pending_more_boundary_and_negative_samples",
        )
        self.assertIn("BM25 仅作为诊断计数",format_markdown(report))


if __name__=="__main__":
    unittest.main()
