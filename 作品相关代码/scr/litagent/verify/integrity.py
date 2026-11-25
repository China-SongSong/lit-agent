"""文献真实性/状态核验引擎。

核验结论（verdict）由本文件的确定性代码计算，大模型只复述带出处的证据、
不下判断。这是防幻觉的架构保证：推理在代码里，语言表达才交给模型。

判定分五档：retracted / verified / uncertain / not_found / suspicious。
其中 ``suspicious``（疑似伪造）门槛设得极高——「查不到」绝不等于「伪造」，
真实中文文献可能因知网/万方/维普收录差异而三家都查不到，这是正常现象。
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from litagent.config import Settings
from litagent.config import settings as default_settings
from litagent.schema import Document, has_cjk, normalize_doi, title_similarity

# 五档结论常量
VERDICT_RETRACTED = "retracted"
VERDICT_VERIFIED = "verified"
VERDICT_UNCERTAIN = "uncertain"
VERDICT_NOT_FOUND = "not_found"
VERDICT_SUSPICIOUS = "suspicious"

_VERDICT_LABELS = {
    VERDICT_RETRACTED: "已撤稿",
    VERDICT_VERIFIED: "真实存在",
    VERDICT_UNCERTAIN: "无法确认",
    VERDICT_NOT_FOUND: "查无此文",
    VERDICT_SUSPICIOUS: "疑似伪造",
}

# 标题相似度阈值：高于此值才认为「题录命中」。
_TITLE_MATCH_THRESHOLD = 0.8

# 有 DOI 时并行查询的权威源。
_DOI_SOURCES = ("crossref", "openalex")


@dataclass
class Evidence:
    """一条核验证据，带出处，可点开 URL 复核。"""

    source: str
    claim: str
    url: str
    matched: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "claim": self.claim,
            "url": self.url,
            "matched": self.matched,
        }


@dataclass
class VerificationResult:
    """一次核验的完整结果。"""

    verdict: str
    confidence: float
    evidence: List[Evidence] = field(default_factory=list)
    sources_queried: List[str] = field(default_factory=list)
    note: str = ""
    checked_at: str = ""

    @property
    def label(self) -> str:
        return _VERDICT_LABELS.get(self.verdict, self.verdict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "verdict_label": self.label,
            "confidence": round(self.confidence, 4),
            "evidence": [e.to_dict() for e in self.evidence],
            "sources_queried": list(self.sources_queried),
            "note": self.note,
            "checked_at": self.checked_at,
        }

    def to_compact_dict(self) -> Dict[str, Any]:
        """体积受控的序列化：只保留 verdict/confidence/note/前 2 条 evidence。

        供 Agent 上下文使用——完整 evidence 会撞上 ``agent_observation_chars``
        截断，反而不如精简后的信息有用。
        """
        evidence = [
            {"source": e.source, "claim": e.claim, "url": e.url}
            for e in self.evidence[:2]
        ]
        return {
            "verdict": self.verdict,
            "verdict_label": self.label,
            "confidence": round(self.confidence, 4),
            "note": self.note,
            "evidence": evidence,
        }


class Verifier:
    """文献真实性核验器。判断逻辑全部在此，不依赖大模型。"""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or default_settings

    # -- 入口 -------------------------------------------------------------
    def verify(self, doc: Document, *, budget_seconds: float = 20.0) -> VerificationResult:
        """核验一篇文献，返回五档结论 + 证据。"""
        doi = normalize_doi(doc.doi)
        if doi:
            return self._verify_by_doi(doc, doi, budget_seconds)
        # 无合法 DOI：走题录交叉核验；DOI 字段存在但格式非法是「疑似伪造」的一个信号。
        doi_invalid = bool(doc.doi and not doi)
        return self._verify_by_bibliography(doc, budget_seconds, doi_invalid=doi_invalid)

    # -- 有 DOI：权威源判定 ------------------------------------------------
    def _verify_by_doi(self, doc: Document, doi: str, budget_seconds: float) -> VerificationResult:
        results: Dict[str, Tuple[Optional[Document], Optional[str]]] = {}
        with ThreadPoolExecutor(max_workers=len(_DOI_SOURCES)) as pool:
            futures = {
                pool.submit(self._query_doi_source, name, doi): name
                for name in _DOI_SOURCES
            }
            deadline = time.monotonic() + budget_seconds
            for future in as_completed(futures, timeout=budget_seconds):
                name = futures[future]
                try:
                    fetched, error = future.result()
                except Exception as exc:  # noqa: BLE001
                    fetched, error = None, "%s: %s" % (type(exc).__name__, str(exc)[:120])
                results[name] = (fetched, error)
                if time.monotonic() > deadline:
                    break

        evidence: List[Evidence] = []
        retracted = False
        retraction_note = ""
        hits = 0
        for name in _DOI_SOURCES:
            fetched, error = results.get(name, (None, "未在预算内完成"))
            if fetched is None:
                if error:
                    evidence.append(Evidence(
                        source=name, claim="查询失败或未收录：%s" % error,
                        url="", matched=False,
                    ))
                else:
                    evidence.append(Evidence(
                        source=name, claim="未收录该 DOI",
                        url="https://doi.org/%s" % doi, matched=False,
                    ))
                continue
            hits += 1
            evidence.append(Evidence(
                source=name,
                claim=self._describe_match(fetched, doc, name),
                url=fetched.url or ("https://doi.org/%s" % doi),
                matched=True,
            ))
            if fetched.retracted:
                retracted = True
                retraction_note = (fetched.extra or {}).get("retraction_note") or "该文献已被撤稿"

        sources_queried = list(results.keys()) or list(_DOI_SOURCES)
        if retracted:
            return VerificationResult(
                verdict=VERDICT_RETRACTED, confidence=1.0,
                evidence=evidence, sources_queried=sources_queried,
                note=retraction_note, checked_at=_now(),
            )
        if hits >= 2:
            return VerificationResult(
                verdict=VERDICT_VERIFIED, confidence=0.9,
                evidence=evidence, sources_queried=sources_queried,
                note="Crossref 与 OpenAlex 均收录且未标记撤稿。",
                checked_at=_now(),
            )
        if hits == 1:
            return VerificationResult(
                verdict=VERDICT_UNCERTAIN, confidence=0.5,
                evidence=evidence, sources_queried=sources_queried,
                note="仅一个权威源收录，另一源未收录或查询失败，无法交叉确认。",
                checked_at=_now(),
            )
        # 两源都未命中：可能是 DOI 不存在，也可能是网络/限流问题，无法区分，诚实标 uncertain。
        return VerificationResult(
            verdict=VERDICT_UNCERTAIN, confidence=0.3,
            evidence=evidence, sources_queried=sources_queried,
            note="Crossref 与 OpenAlex 均未返回该 DOI，可能是 DOI 不存在或网络问题，请人工核实。",
            checked_at=_now(),
        )

    # -- 无 DOI / 中文：题录交叉核验 ----------------------------------------
    def _verify_by_bibliography(
        self, doc: Document, budget_seconds: float, *, doi_invalid: bool
    ) -> VerificationResult:
        from litagent.fetch.opencli import OpenCLISource

        opencli = OpenCLISource(self.settings)
        try:
            availability = opencli.availability()
            if not availability.available:
                return VerificationResult(
                    verdict=VERDICT_UNCERTAIN, confidence=0.0,
                    sources_queried=["opencli"],
                    note="浏览器桥不可用：%s" % availability.detail,
                    checked_at=_now(),
                )
            connected, detail = opencli.bridge_status()
            if not connected:
                return VerificationResult(
                    verdict=VERDICT_UNCERTAIN, confidence=0.0,
                    sources_queried=["opencli"],
                    note=detail, checked_at=_now(),
                )

            query = self._build_query(doc)
            candidates, errors = opencli.search_with_errors(query, limit=3)
        except Exception as exc:  # noqa: BLE001
            return VerificationResult(
                verdict=VERDICT_UNCERTAIN, confidence=0.0,
                sources_queried=["opencli"],
                note="中文源核验失败：%s: %s" % (type(exc).__name__, str(exc)[:160]),
                checked_at=_now(),
            )
        finally:
            opencli.close()

        evidence: List[Evidence] = []
        matched_sites: set[str] = set()
        for candidate in candidates:
            site = str((candidate.extra or {}).get("opencli_site") or "opencli")
            sim = title_similarity(candidate.title, doc.title)
            author_ok = self._author_matches(candidate, doc)
            matched = sim >= _TITLE_MATCH_THRESHOLD
            if matched:
                matched_sites.add(site)
            claim = "标题相似度 %.2f，第一作者%s" % (
                sim, "一致" if author_ok else "不一致或缺失",
            )
            evidence.append(Evidence(
                source=site, claim=claim,
                url=candidate.url or "", matched=matched,
            ))

        sources_queried = list({str((c.extra or {}).get("opencli_site") or "opencli") for c in candidates}) or ["opencli"]

        # 判定
        n_hits = len(matched_sites)
        if doi_invalid and n_hits == 0:
            return VerificationResult(
                verdict=VERDICT_SUSPICIOUS, confidence=0.6,
                evidence=evidence, sources_queried=sources_queried,
                note="DOI 格式非法，且多个中文源均未检索到该题录，存在伪造嫌疑，请人工核实。",
                checked_at=_now(),
            )
        if n_hits >= 2:
            return VerificationResult(
                verdict=VERDICT_VERIFIED, confidence=0.8,
                evidence=evidence, sources_queried=sources_queried,
                note="%d 个源命中且题录一致。" % n_hits,
                checked_at=_now(),
            )
        if n_hits == 1:
            return VerificationResult(
                verdict=VERDICT_UNCERTAIN, confidence=0.4,
                evidence=evidence, sources_queried=sources_queried,
                note="仅 1 个源命中，不足以交叉确认。",
                checked_at=_now(),
            )
        # 0 命中：中文库收录差异是常态，不代表伪造。
        extra_note = "（另：DOI 字段格式非法）" if doi_invalid else ""
        return VerificationResult(
            verdict=VERDICT_UNCERTAIN, confidence=0.2,
            evidence=evidence, sources_queried=sources_queried,
            note="所查询的中文源均未收录该题录，无法确认其是否存在%s。这可能是库收录差异，不代表文献伪造。" % extra_note,
            checked_at=_now(),
        )

    # -- 辅助 --------------------------------------------------------------
    def _query_doi_source(self, name: str, doi: str) -> Tuple[Optional[Document], Optional[str]]:
        """查询单个 DOI 源，返回 (文档或None, 错误或None)。"""
        from litagent.fetch.base import get_source

        try:
            source = get_source(name, self.settings)
            try:
                return source.by_doi(doi), None
            finally:
                source.close()
        except Exception as exc:  # noqa: BLE001
            return None, "%s: %s" % (type(exc).__name__, str(exc)[:120])

    @staticmethod
    def _first_author_family(doc: Document) -> str:
        if doc.authors:
            return (doc.authors[0].family or "").strip()
        return ""

    def _author_matches(self, candidate: Document, doc: Document) -> bool:
        cand = self._first_author_family(candidate)
        origin = self._first_author_family(doc)
        if not cand or not origin:
            return False
        return cand.lower() == origin.lower()

    @staticmethod
    def _describe_match(fetched: Document, doc: Document, source_name: str) -> str:
        sim = title_similarity(fetched.title, doc.title)
        parts = ["命中，标题相似度 %.2f" % sim]
        if fetched.retracted:
            parts.append("该源标记已撤稿")
        if fetched.status and fetched.status != "active":
            parts.append("状态 %s" % fetched.status)
        return "；".join(parts)

    @staticmethod
    def _build_query(doc: Document) -> str:
        from litagent.fetch.opencli import chinese_metadata_query

        author = Verifier._first_author_family(doc)
        if has_cjk(doc.title):
            base = " ".join(p for p in (doc.title, author) if p)
            return chinese_metadata_query(base) or doc.title
        parts = [doc.title]
        if author:
            parts.append(author)
        if doc.year:
            parts.append(str(doc.year))
        return " ".join(parts)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


__all__ = [
    "Evidence",
    "VerificationResult",
    "Verifier",
    "VERDICT_RETRACTED",
    "VERDICT_VERIFIED",
    "VERDICT_UNCERTAIN",
    "VERDICT_NOT_FOUND",
    "VERDICT_SUSPICIOUS",
]
