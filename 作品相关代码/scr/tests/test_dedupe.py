"""跨文献去重主记录切换与全文标记测试。"""
from __future__ import annotations

import unittest

from litagent.clean.dedupe import dedupe_documents
from litagent.index.store import Store
from litagent.schema import Document


class DedupeTests(unittest.TestCase):
    def test_richer_incoming_record_marks_old_keeper_as_consumed(self)->None:
        poor=Document(
            id="local:poor",
            title="Same Paper",
            doi="10.9000/poor",
            abstract="short abstract",
        )
        rich=Document(
            id="doi:10.9000/rich",
            title="Same Paper",
            doi="10.9000/rich",
            fulltext="x"*300,
            extra={"content_level":"fulltext"},
        )
        unique,report=dedupe_documents([poor,rich],use_simhash=False)
        self.assertEqual([doc.id for doc in unique],[rich.id])
        self.assertEqual(report.groups[0].keeper.id,rich.id)
        self.assertEqual(
            [doc.id for doc in report.groups[0].duplicates],
            [poor.id],
        )

    def test_abstract_fallback_does_not_set_fulltext_flag(self)->None:
        doc=Document(
            id="doi:test.abstract",
            title="Abstract only",
            fulltext="Title plus abstract fallback",
            extra={"content_level":"abstract"},
        )
        row=Store._doc_to_row(doc)
        self.assertEqual(row[-1],0)


if __name__=="__main__":
    unittest.main()
