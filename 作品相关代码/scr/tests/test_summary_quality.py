"""总结完整度应按可用内容级别解释。"""
from __future__ import annotations

import unittest

from litagent.schema import Claim,Document,Summary,summary_quality


class SummaryQualityTests(unittest.TestCase):
    def test_title_level_is_not_reported_as_generation_failure(self)->None:
        summary=Summary(
            doc_id="doi:title",
            tldr="只有题名可供概述。",
            claims=[Claim(text="该文献讨论候选发现机制。")],
        )
        doc=Document(
            id="doi:title",
            title="Title only",
            extra={"content_level":"title"},
        )
        status,missing=summary_quality(summary,doc)
        self.assertEqual(status,"title_limited")
        self.assertEqual(len(missing),5)

    def test_abstract_level_is_labeled_separately(self)->None:
        summary=Summary(doc_id="doi:abstract",tldr="摘要概括。")
        doc=Document(
            id="doi:abstract",
            title="Abstract only",
            extra={"content_level":"abstract"},
        )
        status,_=summary_quality(summary,doc)
        self.assertEqual(status,"abstract_limited")

    def test_complete_summary_remains_complete(self)->None:
        summary=Summary(
            doc_id="doi:full",
            tldr="一句话",
            problem="问题",
            methods="方法",
            findings="发现",
            contributions="贡献",
            limitations="局限",
        )
        doc=Document(
            id="doi:full",
            title="Full text",
            extra={"content_level":"fulltext"},
        )
        status,missing=summary_quality(summary,doc)
        self.assertEqual(status,"complete")
        self.assertEqual(missing,[])

    def test_short_content_without_content_level_is_labeled(self)->None:
        summary=Summary(doc_id="doi:short",tldr="")
        doc=Document(id="doi:short",title="短正文文献")
        status,missing=summary_quality(
            summary,doc,content_chars=127
        )
        self.assertEqual(status,"short_content_limited")
        self.assertEqual(len(missing),6)


if __name__=="__main__":
    unittest.main()
