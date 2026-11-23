"""跨层数据契约与不可绕过的溯源边界。

本模块只定义稳定契约，不负责具体网络或浏览器实现。后续接入 OpenCLI
和向量检索时，必须复用这里的来源、候选和决策字段，避免不同入口各自
解释“候选”“旁证”和“找到出处”。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from litagent.schema import Document, TraceResult

CONTRACT_VERSION="1"


class ContractViolation(RuntimeError):
    """调用方违反溯源或联网契约。"""


class OnlineAccessDenied(ContractViolation):
    """未获得显式联网许可却尝试调用外部来源。"""


class SourceTier(str,Enum):
    """候选来源的稳定层级，不表示具体排序权重。"""

    LOCAL="local"
    PRIMARY_API="primary_api"
    BROWSER_SUPPLEMENT="browser_supplement"
    EXTERNAL_OTHER="external_other"


class CandidateKind(str,Enum):
    """候选只能证明“可能相关”，不能冒充本地原文证据。"""

    LOCAL_HIT="local_hit"
    ONLINE_CANDIDATE="online_candidate"


class DecisionBasis(str,Enum):
    """确认找到出处时所依据的唯一主证据类型。"""

    NONE="none"
    LOCAL_FINGERPRINT="local_fingerprint"


@dataclass(frozen=True)
class OnlineRequest:
    """一次明确的联网请求。

    ``allow_online`` 是权限边界，不是网络可用性探测结果。调用外部 API
    或浏览器源之前必须调用 :meth:`require_online`。
    """

    query:str
    allow_online:bool
    purpose:str="trace"
    deadline_seconds:Optional[float]=None

    def require_online(self)->"OnlineRequest":
        if not str(self.query or "").strip():
            raise ContractViolation("联网请求缺少非空查询文本")
        if not self.allow_online:
            raise OnlineAccessDenied("没有显式联网许可，禁止调用外部数据源")
        if self.deadline_seconds is not None and self.deadline_seconds<=0:
            raise ContractViolation("联网截止时间必须大于 0")
        return self


@dataclass(frozen=True)
class SupportSignals:
    """不改变 found 判定的旁证记录。"""

    bm25:bool=False
    vector:bool=False
    same_chunk_id:bool=True

    def to_dict(self)->Dict[str,Any]:
        return{
            "bm25":bool(self.bm25),
            "vector":bool(self.vector),
            "same_chunk_id":bool(self.same_chunk_id),
        }


def source_tier_for(source:str)->SourceTier:
    """把来源名映射到稳定层级。

    当前四个结构化 API 属于主通道；未来 OpenCLI 候选使用浏览器补充层。
    未登记来源保留为其他外部来源，不能因此获得本地证据权限。
    """
    key=(source or "").strip().lower()
    if key in("openalex","crossref","arxiv","semanticscholar"):
        return SourceTier.PRIMARY_API
    if key=="opencli" or key.startswith("opencli:"):
        return SourceTier.BROWSER_SUPPLEMENT
    return SourceTier.EXTERNAL_OTHER


@dataclass
class SourceCandidate:
    """一个外部来源候选的统一表示。"""

    provider:str
    source:str
    source_tier:SourceTier
    document:Document
    score:Optional[float]=None
    retrieved_at:str=""
    raw:Dict[str,Any]=field(default_factory=dict)
    candidate_kind:CandidateKind=CandidateKind.ONLINE_CANDIDATE

    @property
    def is_local_evidence(self)->bool:
        return False

    def to_public_dict(self,*,citation:str="")->Dict[str,Any]:
        return{
            "provider":self.provider,
            "source":self.source,
            "source_tier":self.source_tier.value,
            "candidate_kind":self.candidate_kind.value,
            "is_local_evidence":False,
            "id":self.document.id,
            "title":self.document.title,
            "authors":[a.display for a in self.document.authors[:5]],
            "year":self.document.year,
            "venue":self.document.venue,
            "doi":self.document.doi,
            "url":self.document.url,
            "abstract":self.document.abstract or "",
            "pdf_url":str((self.document.extra or{}).get("pdf_url") or ""),
            "has_fulltext":bool((self.document.fulltext or "").strip()),
            "content_level":str(
                (self.document.extra or{}).get("content_level") or ""
            ),
            "discipline_tags":list(self.document.discipline_tags),
            "is_oa":self.document.is_oa,
            "score":self.score,
            "retrieved_at":self.retrieved_at,
            "note":"本地库未收录，以下为联网候选，请自行核对原文",
            "citation":citation,
        }


def validate_trace_result(result:TraceResult)->TraceResult:
    """强制执行“只有本地指纹证据可以确认出处”的硬约束。"""
    if result.found:
        if result.decision_basis!=DecisionBasis.LOCAL_FINGERPRINT.value:
            raise ContractViolation(
                "found=true 只能由本地指纹证据决定，"
                "当前 decision_basis=%r"%result.decision_basis
            )
        if result.hit is None or not result.hit.chunk_id or not result.hit.doc_id:
            raise ContractViolation("found=true 但缺少本地片段证据")
        if "phrase" not in result.hit.sources:
            raise ContractViolation("found=true 的主命中断言缺少 phrase 指纹来源")
        if not str(result.matched_text or "").strip():
            raise ContractViolation("found=true 但缺少实际匹配文本")
        if result.level not in("local_exact","local_fuzzy"):
            raise ContractViolation(
                "found=true 的等级必须是 local_exact/local_fuzzy，"
                "当前为 %r"%result.level
            )

    for candidate in result.online_candidates:
        if candidate.get("is_local_evidence"):
            raise ContractViolation("联网候选不能标记为本地证据")
        if candidate.get("candidate_kind") not in(
            None,CandidateKind.ONLINE_CANDIDATE.value
        ):
            raise ContractViolation("联网候选的 candidate_kind 非法")
    return result


__all__=[
    "CONTRACT_VERSION",
    "ContractViolation",
    "OnlineAccessDenied",
    "SourceTier",
    "CandidateKind",
    "DecisionBasis",
    "OnlineRequest",
    "SupportSignals",
    "SourceCandidate",
    "source_tier_for",
    "validate_trace_result",
]
