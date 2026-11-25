"""文献真实性/状态核验的回归测试。

覆盖：撤稿检测、状态派生、五档 verdict、体积控制、诚实性（无法确认≠伪造）、
以及防幻觉红线是否写入系统提示词。

网络调用通过 mock 隔离，本文件全部可离线运行。
"""
from __future__ import annotations

import unittest
from unittest import mock

from litagent.fetch.crossref import _detect_retraction
from litagent.schema import Document, derive_document_status
from litagent.verify import (
    VERDICT_RETRACTED,
    VERDICT_SUSPICIOUS,
    VERDICT_UNCERTAIN,
    VERDICT_VERIFIED,
    Evidence,
    VerificationResult,
    Verifier,
)


class DocumentStatusTests(unittest.TestCase):
    """Document.retracted / status 只读派生（不落库、不进 to_dict）。"""

    def test_retracted_derives_from_extra(self)->None:
        doc=Document(id="x",title="t",extra={"is_retracted":True})
        self.assertTrue(doc.retracted)

    def test_retracted_defaults_false(self)->None:
        doc=Document(id="x",title="t",extra={})
        self.assertFalse(doc.retracted)

    def test_status_defaults_active(self)->None:
        doc=Document(id="x",title="t",extra={})
        self.assertEqual(doc.status,"active")

    def test_status_derives_from_extra(self)->None:
        doc=Document(id="x",title="t",extra={"status":"retracted"})
        self.assertEqual(doc.status,"retracted")

    def test_retracted_not_in_to_dict(self)->None:
        # property 不应被 asdict 序列化——它是只读派生，不是 dataclass 字段。
        doc=Document(id="x",title="t",extra={"is_retracted":True})
        self.assertNotIn("retracted",doc.to_dict())
        self.assertNotIn("status",doc.to_dict())


class DeriveStatusTests(unittest.TestCase):
    def test_retracted(self)->None:
        self.assertEqual(derive_document_status(True,"article"),"retracted")

    def test_preprint(self)->None:
        self.assertEqual(derive_document_status(False,"posted-content"),"preprint")

    def test_erratum(self)->None:
        self.assertEqual(derive_document_status(False,"erratum"),"erratum")

    def test_active(self)->None:
        self.assertEqual(derive_document_status(False,"journal-article"),"active")


class RetractionDetectionTests(unittest.TestCase):
    def test_assertion_retraction(self)->None:
        item={"assertion":[{"name":"retraction","value":"retraction","label":"Retraction"}]}
        retracted,note=_detect_retraction(item)
        self.assertTrue(retracted)
        self.assertTrue(note)

    def test_update_to_retraction(self)->None:
        item={"update-to":[{"type":"retraction","label":"Retracted"}]}
        retracted,_=_detect_retraction(item)
        self.assertTrue(retracted)

    def test_relation_retraction(self)->None:
        item={"relation":{"has-retraction":[{"id":"x"}]}}
        retracted,_=_detect_retraction(item)
        self.assertTrue(retracted)

    def test_normal_item(self)->None:
        retracted,note=_detect_retraction({"title":"ok"})
        self.assertFalse(retracted)
        self.assertIsNone(note)


class VerificationResultTests(unittest.TestCase):
    def test_compact_limits_evidence_to_two(self)->None:
        r=VerificationResult(
            verdict=VERDICT_VERIFIED,confidence=0.9,
            evidence=[
                Evidence("a","c1","u1"),Evidence("b","c2","u2"),Evidence("c","c3","u3"),
            ],
        )
        self.assertEqual(len(r.to_compact_dict()["evidence"]),2)

    def test_label_mapping(self)->None:
        self.assertEqual(VerificationResult(VERDICT_RETRACTED,1.0).label,"已撤稿")
        self.assertEqual(VerificationResult(VERDICT_VERIFIED,1.0).label,"真实存在")
        self.assertEqual(VerificationResult(VERDICT_UNCERTAIN,0.0).label,"无法确认")
        self.assertEqual(VerificationResult(VERDICT_SUSPICIOUS,0.6).label,"疑似伪造")


class VerifierDecisionTests(unittest.TestCase):
    """Verifier 的确定性判定逻辑（mock 掉网络）。"""

    @staticmethod
    def _doc(doi=None,title="测试文献",year=2020)->Document:
        return Document(
            id=Document.make_id("verify",None,doi,title),
            title=title,year=year,doi=doi,
        )

    def test_retracted_when_source_marks_retracted(self)->None:
        v=Verifier()
        fetched=Document(id="doi:x",title="测试文献",
                         extra={"is_retracted":True,"status":"retracted"})
        with mock.patch.object(v,"_query_doi_source",side_effect=[
            (fetched,None),(fetched,None),
        ]):
            result=v.verify(self._doc(doi="10.1234/test"))
        self.assertEqual(result.verdict,VERDICT_RETRACTED)

    def test_verified_when_two_sources_hit(self)->None:
        v=Verifier()
        fetched=Document(id="doi:x",title="测试文献",extra={})
        with mock.patch.object(v,"_query_doi_source",side_effect=[
            (fetched,None),(fetched,None),
        ]):
            result=v.verify(self._doc(doi="10.1234/test"))
        self.assertEqual(result.verdict,VERDICT_VERIFIED)

    def test_uncertain_when_no_source_hit(self)->None:
        v=Verifier()
        with mock.patch.object(v,"_query_doi_source",side_effect=[
            (None,"not found"),(None,"not found"),
        ]):
            result=v.verify(self._doc(doi="10.1234/test"))
        self.assertEqual(result.verdict,VERDICT_UNCERTAIN)

    def test_bibliography_zero_hit_is_uncertain_not_suspicious(self)->None:
        """诚实性：中文源均未收录且无 DOI 非法信号 → uncertain，不是 suspicious。"""
        v=Verifier()
        doc=self._doc(doi=None,title="一篇真实的中文文献")
        fake=mock.MagicMock()
        fake.availability.return_value=mock.MagicMock(available=True)
        fake.bridge_status.return_value=(True,"connected")
        fake.search_with_errors.return_value=([],["cnki: 0 条"])
        with mock.patch("litagent.fetch.opencli.OpenCLISource",return_value=fake):
            result=v.verify(doc)
        self.assertEqual(result.verdict,VERDICT_UNCERTAIN)

    def test_bibliography_doi_invalid_zero_hit_is_suspicious(self)->None:
        """DOI 格式非法 + 多源零命中 → suspicious（疑似伪造的强信号）。"""
        v=Verifier()
        doc=self._doc(doi="not-a-doi",title="一篇中文文献")
        fake=mock.MagicMock()
        fake.availability.return_value=mock.MagicMock(available=True)
        fake.bridge_status.return_value=(True,"connected")
        fake.search_with_errors.return_value=([],[])
        with mock.patch("litagent.fetch.opencli.OpenCLISource",return_value=fake):
            result=v.verify(doc)
        self.assertEqual(result.verdict,VERDICT_SUSPICIOUS)

    def test_bibliography_bridge_offline_is_uncertain(self)->None:
        """浏览器桥离线时中文核验能力失效，诚实标 uncertain，不阻断。"""
        v=Verifier()
        doc=self._doc(doi=None,title="一篇中文文献")
        fake=mock.MagicMock()
        fake.availability.return_value=mock.MagicMock(available=False,detail="未配置")
        with mock.patch("litagent.fetch.opencli.OpenCLISource",return_value=fake):
            result=v.verify(doc)
        self.assertEqual(result.verdict,VERDICT_UNCERTAIN)


class PromptRedlineTests(unittest.TestCase):
    """防幻觉红线必须写进系统提示词。"""

    def test_prompt_contains_verification_redline(self)->None:
        from litagent.agent.prompts import AGENT_SYSTEM_TEMPLATE

        self.assertIn("verification",AGENT_SYSTEM_TEMPLATE)
        self.assertIn("绝不允许",AGENT_SYSTEM_TEMPLATE)
        self.assertIn("已撤稿",AGENT_SYSTEM_TEMPLATE)
        self.assertIn("无法确认",AGENT_SYSTEM_TEMPLATE)


class VerifyCitationToolTests(unittest.TestCase):
    """verify_citation 工具的参数校验（不联网）。"""

    def test_requires_title_or_doc_id(self)->None:
        from litagent.agent.tools import LiteratureTools

        tools=LiteratureTools.__new__(LiteratureTools)  #跳过构造，只测方法逻辑
        result=LiteratureTools.verify_citation(tools)
        self.assertIn("error",result)


if __name__=="__main__":
    unittest.main()
