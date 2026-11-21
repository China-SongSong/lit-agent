"""Issues.md 第十一轮观察项回归测试。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import requests
from litagent.cite.gbt7714 import format_ris
from litagent.clean.normalize import detect_language,remove_invisible
from litagent.disciplines import classify_disciplines
from litagent.fetch.arxiv import parse_journal_ref
from litagent.fetch.base import HttpClient,HttpError


class IssueObservationTests(unittest.TestCase):
    def test_http_error_exposes_retryable_flag(self)->None:
        self.assertTrue(
            HttpError("HTTP 503",status=503,retryable=True).retryable
        )
        self.assertFalse(HttpError("HTTP 404",status=404).retryable)

    def test_connection_failure_marks_retryable(self)->None:
        client=HttpClient(max_retries=0,use_cache=False)
        with patch.object(
            client._session,"request",
            side_effect=requests.ConnectionError("offline"),
        ):
            with self.assertRaises(HttpError) as caught:
                client.request("GET","https://example.invalid")
        self.assertTrue(caught.exception.retryable)

    def test_ris_uses_primary_title_and_year_aliases(self)->None:
        output=format_ris({
            "title":"A journal article",
            "year":2024,
            "venue":"Example Journal",
            "authors":[{"family":"Doe","given":"Jane"}],
        })
        self.assertIn("T1  - A journal article",output)
        self.assertIn("Y1  - 2024",output)
        self.assertNotIn("TI  -",output)
        self.assertNotIn("PY  -",output)

    def test_direction_control_is_removed(self)->None:
        self.assertEqual(
            remove_invisible("前\u200e后\u200f文\u202e字"),
            "前后文字",
        )

    def test_japanese_with_kana_is_not_labeled_chinese(self)->None:
        self.assertEqual(
            detect_language("これは日本語の研究結果です。"),
            "ja",
        )
        self.assertEqual(
            detect_language("这是中文的研究结果。"),
            "zh",
        )

    def test_arxiv_journal_ref_accepts_year_followed_by_pages(self)->None:
        self.assertEqual(
            parse_journal_ref("Journal of Testing 12 (2020) 123-130"),
            ("Journal of Testing",2020),
        )
        self.assertEqual(
            parse_journal_ref(
                "Journal of Testing, 12, 2020, 123-130"
            ),
            ("Journal of Testing",2020),
        )

    def test_data_citation_belongs_to_information_science(self)->None:
        self.assertIn(
            "图书情报与档案管理类",
            classify_disciplines(
                title="研究数据引用与数据集计量",
                abstract="Data citation and dataset citation practices",
            ),
        )


if __name__=="__main__":
    unittest.main()
