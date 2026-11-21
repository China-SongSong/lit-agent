"""学科标签与查询历史回归测试。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from litagent.disciplines import GENERAL_DISCIPLINE,classify_disciplines
from litagent.index.store import Store
from litagent.schema import Document


class DisciplineClassifierTests(unittest.TestCase):
    def test_chinese_discipline_examples(self)->None:
        cases={
            "传播学视野下的网络舆论与新闻生产":"新闻传播学类",
            "社会分层与社区人口流动研究":"社会学类",
            "近代史史料中的城市考古与历史记忆":"历史学类",
            "引文分析与文献计量中的知识图谱":"图书情报与档案管理类",
            "机器学习算法与神经网络模型":"计算机科学类",
            "锂离子电池热失控与储能安全":"工程技术类",
            "聚焦网络爬虫与网页抓取":"计算机科学类",
            "蓄电池储能系统热管理":"工程技术类",
            "分布式增量爬虫与网页蜘蛛":"计算机科学类",
        }
        for title,expected in cases.items():
            self.assertIn(
                expected,classify_disciplines(title=title,abstract=title)
            )

    def test_english_and_general_fallback(self)->None:
        self.assertIn(
            "计算机科学类",
            classify_disciplines(
                title="Machine learning for neural network systems"
            ),
        )
        self.assertIn(
            "计算机科学类",
            classify_disciplines(title="Adaptive focused web crawler"),
        )
        self.assertIn(
            "工程技术类",
            classify_disciplines(
                title="Lithium-ion battery thermal runaway and energy storage"
            ),
        )
        self.assertIn(
            "工程技术类",
            classify_disciplines(title="Li-ion battery storage safety"),
        )
        self.assertIn(
            "计算机科学类",
            classify_disciplines(title="Distributed incremental crawling"),
        )
        self.assertEqual(
            classify_disciplines(title="An experimental study of cultivated plants"),
            [GENERAL_DISCIPLINE],
        )

    def test_document_gets_tags_automatically(self)->None:
        doc=Document(id="doi:10.1/test",title="新闻传播与媒体舆论")
        self.assertEqual(doc.discipline_tags,["新闻传播学类"])
        restored=Document.from_dict(doc.to_dict())
        self.assertEqual(restored.discipline_tags,["新闻传播学类"])


class TagAndHistoryStoreTests(unittest.TestCase):
    def test_tags_persist_backfill_and_history_round_trip(self)->None:
        with tempfile.TemporaryDirectory(prefix="litagent-tags-") as temp:
            store=Store(Path(temp)/"index.db")
            try:
                store.init_schema()
                doc=Document(
                    id="doi:10.1/social",
                    title="社会分层与社区人口流动",
                    abstract="分析社会网络与社会不平等。",
                )
                store.upsert_document(doc)
                self.assertIn("社会学类",store.get_document_tags(doc.id))

                # 模拟旧库缺少标签：再次初始化时会自动补齐。
                store.connect().execute(
                    "DELETE FROM document_tags WHERE doc_id=?",(doc.id,)
                )
                store.init_schema()
                self.assertIn("社会学类",store.get_document_tags(doc.id))

                history_id=store.record_query_history(
                    "trace","社会分层与社区人口流动",
                    found=True,level="local_exact",result_count=1,
                    metadata={"source_hint":"10.1/social"},
                )
                rows=store.list_query_history(limit=10,kind="trace")
                self.assertEqual(rows[0]["id"],history_id)
                self.assertTrue(rows[0]["found"])
                self.assertEqual(rows[0]["metadata"]["source_hint"],"10.1/social")
                self.assertEqual(store.delete_query_history(history_id),1)
                self.assertEqual(store.list_query_history(),[])

                store.record_query_history("agent","找一个社会学主题")
                self.assertEqual(store.clear_query_history(kind="agent"),1)
            finally:
                store.close_all()


if __name__=="__main__":
    unittest.main()
