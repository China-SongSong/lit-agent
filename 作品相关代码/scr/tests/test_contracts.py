"""契约与不可绕过溯源边界的单元测试。"""
from __future__ import annotations

import unittest

from litagent.contracts import(
    ContractViolation,
    DecisionBasis,
    OnlineAccessDenied,
    OnlineRequest,
    SourceCandidate,
    SourceTier,
    validate_trace_result,
)
from litagent.schema import Author,Document,SearchHit,TraceResult


class ContractTests(unittest.TestCase):
    def test_offline_request_is_denied(self)->None:
        request=OnlineRequest(query="test",allow_online=False)
        with self.assertRaises(OnlineAccessDenied):
            request.require_online()

    def test_online_request_is_allowed(self)->None:
        request=OnlineRequest(query="test",allow_online=True)
        self.assertIs(request.require_online(),request)

    def test_found_requires_local_fingerprint(self)->None:
        doc=Document(id="doi:test",title="Test")
        hit=SearchHit(
            chunk_id="doi:test#0000-abcd",
            doc_id=doc.id,
            score=0.97,
            text="matched source text",
            doc=doc,
            sources=["phrase"],
        )
        result=TraceResult(
            query="matched source text",
            found=True,
            confidence=0.97,
            level="local_exact",
            hit=hit,
            matched_text="matched source text",
            decision_basis=DecisionBasis.LOCAL_FINGERPRINT.value,
        )
        self.assertIs(validate_trace_result(result),result)

    def test_found_without_local_evidence_is_rejected(self)->None:
        result=TraceResult(
            query="test",
            found=True,
            confidence=0.97,
            level="local_exact",
            matched_text="test",
            decision_basis="online",
        )
        with self.assertRaises(ContractViolation):
            validate_trace_result(result)

    def test_online_candidate_cannot_be_local_evidence(self)->None:
        doc=Document(id="doi:test",title="Test",authors=[Author(family="Li")])
        candidate=SourceCandidate(
            provider="openalex",
            source="openalex",
            source_tier=SourceTier.PRIMARY_API,
            document=doc,
        )
        payload=candidate.to_public_dict()
        self.assertFalse(payload["is_local_evidence"])
        self.assertEqual(payload["candidate_kind"],"online_candidate")

        result=TraceResult(
            query="test",
            online_candidates=[{**payload,"is_local_evidence":True}],
        )
        with self.assertRaises(ContractViolation):
            validate_trace_result(result)


if __name__=="__main__":
    unittest.main()
