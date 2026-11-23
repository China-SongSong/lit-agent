"""定位一段引文的来源，并给出置信度、上下文和引用。"""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from litagent.config import Settings
from litagent.config import settings as default_settings
from litagent.contracts import (
    DecisionBasis,
    OnlineRequest,
    SourceCandidate,
    SupportSignals,
    source_tier_for,
    validate_trace_result,
)
from litagent.index.lexical import tokenize
from litagent.index.phrase import PhraseIndex, exact_tokens
from litagent.index.store import Store
from litagent.index.vector import (
    VectorHit,
    VectorIndex,
    get_embedding_encoder,
)
from litagent.schema import Document, SearchHit, TraceResult, has_cjk, normalize_doi

_LOG=logging.getLogger(__name__)
_DOI_IN_TEXT_RE=re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+",re.IGNORECASE)

CONFIDENCE_EXACT=0.85  #指纹对齐到这个比例
CONFIDENCE_FUZZY=0.45
CONFIDENCE_WEAK=0.25

DEFAULT_FOUND_THRESHOLD=0.72  #0.72是确认找到的门槛，宁可漏报也不误报

MIN_TOKENS_FOR_EXACT=8  #绝对长度也要够，8个词元全中可能只是撞上一句套话
SHORT_EXACT_MIN_TOKENS=3  #短查询只要整段精确对齐，就可以单独设置信度下限
SHORT_EXACT_LEVEL_TOKENS=6  #六字以上的完整精确对齐可以进入 exact 档

def _confidence_from_ratio(ratio:float,matched_tokens:int)->float:
    """把对齐比例换算成置信度。

    置信度不再接受 BM25 固定加成。BM25 仍作为诊断旁证记录，但不再
    参与数值计算，避免把已经证伪的 +0.04 机制继续混入阈值校准。
    """
    if ratio<=0:  #比例非正说明压根没对齐上，早退给0
        return 0.0

    span=max(1e-6,1.0-CONFIDENCE_WEAK)  #分母提前算好，末尾用max兜底1e-6
    base=0.30+0.67*min(1.0,max(0.0,(ratio-CONFIDENCE_WEAK)/span)) #线性映射得到基础分

    if matched_tokens<MIN_TOKENS_FOR_EXACT:  #只惩罚短样本，长样本本来就靠比例说话
        base*=0.45+0.055*matched_tokens
        if ratio>=0.999 and matched_tokens>=SHORT_EXACT_MIN_TOKENS:
            base=max(base,0.75)  #四字以内的精确短句不再因线性惩罚被误判为未找到

    return round(min(base,0.97),4)  #封顶0.97：公式最大值与当前证据口径一致

def _extract_keywords(quote:str,*,limit:int=12)->List[str]:
    """从一段话里抽出用于联网检索的关键词"""
    tokens=tokenize(quote)
    if not tokens:
        return []

    # 引号内的并列概念通常是论文的关键术语。先拆开破折号再补进词表，
    # 避免长段落只取开头几个泛词，把“资格确认”等核心概念丢掉。
    quoted_terms:List[str]=[]
    for segment in re.findall(r"[“\"]([^”\"]{2,120})[”\"]",quote):
        quoted_terms.extend(
            term.strip()
            for term in re.split(r"[—–\-]+",segment)
            if term.strip()
        )

    first_positions:Dict[str,int]={}
    for position,token in enumerate(quoted_terms):
        first_positions.setdefault(token,position)
    for position,token in enumerate(tokens):
        first_positions.setdefault(token,len(quoted_terms)+position)

    ordered=sorted(first_positions,key=lambda token:(-len(token),first_positions[token]))
    return ordered[:limit]


def parse_source_hint(hint:str)->Tuple[str,str]:
    """从 DOI、CNKI 链接或其他来源提示中提取不依赖会话参数的标识。"""
    raw=(hint or "").strip()
    if not raw:
        return "",""
    match=_DOI_IN_TEXT_RE.search(raw)
    doi=normalize_doi(match.group(0) if match else raw)
    if doi:
        return "doi",doi
    redacted_match=re.search(
        r"\bCNKI\s+filename\s*=\s*([^\s&]+)",raw,flags=re.IGNORECASE
    )
    if redacted_match:
        return "cnki_filename",redacted_match.group(1).strip()
    try:
        query=parse_qs(urlparse(raw).query)
    except ValueError:
        query={}
    for key,values in query.items():
        if key.lower()=="filename" and values:
            filename=values[0].strip()
            if filename:
                return "cnki_filename",filename
    return "",""


def redact_source_hint(hint:str)->str:
    """生成可写入历史或诊断信息的来源摘要，去掉查询串和会话令牌。"""
    raw=(hint or "").strip()
    kind,value=parse_source_hint(raw)
    if kind=="doi":
        return "DOI: %s"%value
    if kind=="cnki_filename":
        return "CNKI filename=%s"%value
    if not raw:
        return ""
    if re.search(
        r"(?:invoice|nonce|token|session|ticket)=",raw,flags=re.IGNORECASE
    ):
        return "<已脱敏来源提示>"
    try:
        parsed=urlparse(raw)
    except ValueError:
        return raw[:80]
    if parsed.scheme in("http","https") and parsed.netloc:
        return "%s://%s%s"%(parsed.scheme,parsed.netloc,parsed.path)
    return raw[:80]

@dataclass
class TraceReport:
    """溯源过程的诊断信息"""

    quote_chars:int=0
    quote_tokens:int=0  #有效词元数，低于3就直接拒绝溯源
    phrase_candidates:int=0
    bm25_support:bool=False
    vector_candidates:int=0
    vector_error:str=""
    vector_cross_language:int=0
    online_queried:bool=False  #是否真的发起过联网检索
    online_error:str=""
    online_filtered_cross_language:int=0
    online_filtered_low_relevance:int=0
    fingerprint_n:int=0
    spanned_chunks:int=0
    config_warning:str=""  #索引参数与当前配置不一致时的提示
    elapsed_seconds:float=0.0

    def summary(self)->str:
        bits=["%d 字 / %d 词元"%(self.quote_chars,self.quote_tokens),  #第一段固定报字数与词元数
                "指纹 n=%d"%self.fingerprint_n,
                "指纹候选 %d"%self.phrase_candidates]
        if self.bm25_support:
            bits.append("BM25 诊断命中")  #只记录命中事实，不改变置信度
        if self.vector_candidates:
            bits.append("向量候选 %d"%self.vector_candidates)
        elif self.vector_error:
            bits.append("向量未完成")
        if self.vector_cross_language:
            bits.append("跨语种语义候选 %d"%self.vector_cross_language)
        if self.spanned_chunks>1:
            bits.append("跨 %d 个片段拼接"%self.spanned_chunks)  #跨片段才值得说一句
        if self.online_queried:
            bits.append("已联网检索")
        if self.online_filtered_cross_language:
            bits.append("跨语种过滤 %d 条"%self.online_filtered_cross_language)
        if self.online_filtered_low_relevance:
            bits.append("弱相关过滤 %d 条"%self.online_filtered_low_relevance)
        bits.append("%.2f 秒"%self.elapsed_seconds)
        return "溯源诊断："+"，".join(bits)

class QuoteTracer:
    """段落溯源器"""

    def __init__(self,store:Store,settings:Optional[Settings]=None,*,
                 searcher:Optional[Any]=None)->None:
        self.store=store  #索引对象由外部注入，便于测试时换成假索引
        self.settings=settings or default_settings
        #导致全部查询静默返回空
        self.phrase=PhraseIndex(store)
        self._searcher=searcher
        self.vector_encoder:Optional[Any]=None
        self.vector:Optional[VectorIndex]=None
        self.vector_model=""
        self._last_vector_error=""
        self.config_warning:Optional[str]=None
        if(self.phrase.n_from_index  #只在与索引自带参数比对之后才可能被赋值
                and self.phrase.n_from_index!=self.settings.fingerprint_n):
            self.config_warning=(
                "索引使用 n=%d 建立，当前配置是 LIT_FINGERPRINT_N=%d。"
                "已按索引实际参数查询；若要让新配置生效，请执行 "
                "`cli.py index --rebuild`。"
                %(self.phrase.n_from_index,self.settings.fingerprint_n)
            )

    def trace(self,quote:str,*,allow_online:bool=True,
              found_threshold:float=DEFAULT_FOUND_THRESHOLD,
              top_k:int=5,online_interactive:bool=False,
              source_hint:str="")->Tuple[TraceResult,TraceReport]:
        """溯源一段话，并在返回前校验不可绕过的证据约束。"""
        result,report=self._trace(
            quote,
            allow_online=allow_online,
            found_threshold=found_threshold,
            top_k=top_k,
            online_interactive=online_interactive,
            source_hint=source_hint,
        )
        validate_trace_result(result)
        return result,report

    def _trace(self,quote:str,*,allow_online:bool=True,
               found_threshold:float=DEFAULT_FOUND_THRESHOLD,
               top_k:int=5,online_interactive:bool=False,
               source_hint:str="")->Tuple[TraceResult,TraceReport]:
        """实现溯源主流程；外部调用统一走权限守卫。"""
        started=time.monotonic()  #用单调时钟而不是墙上时钟
        report=TraceReport()
        report.fingerprint_n=self.phrase.n
        report.config_warning=self.config_warning or ""
        text=(quote or "").strip()  #统一成去空白后的文本，后面所有环节都用它
        report.quote_chars=len(text)
        if len(text)>self.settings.trace_max_chars:
            result=TraceResult(
                query=text[:200],
                level="input_too_long",
                note=(
                    "输入共 %d 字，超过单次溯源上限 %d 字。"
                    "请截取最关键的连续段落后重试。"
                    %(len(text),self.settings.trace_max_chars)
                ),
            )
            report.elapsed_seconds=time.monotonic()-started
            _LOG.warning(
                "trace rejected length=%d limit=%d elapsed=%.3f",
                len(text),self.settings.trace_max_chars,report.elapsed_seconds,
            )
            return result,report
        result=TraceResult(query=text,note="")
        online_request=OnlineRequest(
            query=text,
            allow_online=allow_online,
            purpose="trace",
            deadline_seconds=(
                self.settings.opencli_interactive_timeout+2.0
                if online_interactive else self.ONLINE_TIMEOUT
            ),
        )

        if not text:
            result.level="empty"  #空输入单独标识，别让上层把“未输入”当成“本地未找到”
            result.note="输入为空。"  #把原因写进note而不是抛异常
            report.elapsed_seconds=time.monotonic()-started
            _LOG.info("trace empty")
            return result,report

        tokens=exact_tokens(text)  #用精确词元切分而不走模糊分支
        report.quote_tokens=len(tokens)
        query_language=self._query_language(text)

        if len(tokens)<3:  #少于3个词元直接拒绝：短片段重合概率太高
            result.note=("输入太短（仅 %d 个有效词元），无法可靠溯源。"
                           "请提供完整的句子或段落。"%len(tokens))
            report.elapsed_seconds=time.monotonic()-started
            return result,report

        #第一级：精确指纹
        candidates=self.phrase.search(text,top_k=max(top_k,5),
                                        min_ratio=CONFIDENCE_WEAK,with_text=True) #召回条数5，下限0.25
        report.phrase_candidates=len(candidates)  #候选数为0是后面走联网分支的判据
        vector_hits:List[VectorHit]=[]
        if(
            self.settings.vector_enabled
            and (
                not candidates
                or candidates[0].score<self.settings.vector_trigger_ratio
            )
        ):
            vector_hits=self._vector_search(text)
            report.vector_candidates=len(vector_hits)
            report.vector_error=self._last_vector_error

        if not candidates:
            if vector_hits:
                semantic_hits=[
                    self._vector_to_search_hit(hit) for hit in vector_hits
                ]
                preferred_hits=[
                    hit for hit in semantic_hits
                    if hit.doc is not None
                    and self._matches_language(hit.doc,query_language)
                ]
                if query_language=="zh" and not preferred_hits:
                    report.vector_cross_language=len(semantic_hits)
                    result.level="local_semantic"
                    result.hit=None
                    result.candidates=semantic_hits[:max(1,top_k)]
                    result.confidence=0.0
                    result.decision_basis=DecisionBasis.NONE.value
                    result.supporting_signals=SupportSignals(
                        bm25=False,vector=True,same_chunk_id=False
                    ).to_dict()
                    result.supporting_signals["vector_similarity"]=round(
                        semantic_hits[0].score,4
                    )
                    result.note=(
                        "本地向量只找到 %d 个跨语种候选，未放入主结果。"
                        "当前本地库没有中文原文证据，建议勾选联网后续查中文来源；"
                        "跨语种语义相似不能证明出处。"
                        %len(semantic_hits)
                    )
                    self._maybe_try_online(
                        text,result,report,online_request,keep_local=True,
                        online_interactive=online_interactive,
                        source_hint=source_hint,
                    )
                    report.elapsed_seconds=time.monotonic()-started
                    _LOG.info(
                        "trace semantic_cross_language candidates=%d online=%s "
                        "elapsed=%.3f",
                        len(semantic_hits),report.online_queried,
                        report.elapsed_seconds,
                    )
                    return result,report
                if preferred_hits:
                    semantic_hits=preferred_hits
                best_semantic=semantic_hits[0]
                result.level="local_semantic"
                result.hit=best_semantic
                result.candidates=semantic_hits[1:max(1,top_k)]
                result.confidence=0.0
                result.decision_basis=DecisionBasis.NONE.value
                result.supporting_signals=SupportSignals(
                    bm25=False,vector=True,same_chunk_id=False
                ).to_dict()
                result.supporting_signals["vector_similarity"]=round(
                    best_semantic.score,4
                )
                result.source_label=self._source_label(best_semantic.doc)
                result.citations=self._citations_for(best_semantic.doc)
                result.matched_text=best_semantic.text[:500]
                result.note=(
                    "本地指纹未命中，向量召回找到语义相关候选"
                    "（相似度 %.0f%%）。向量只能提供检索线索，"
                    "不能证明用户语句与候选原文相同，**不得据此直接引用**。"
                    %(best_semantic.score*100)
                )
                self._maybe_try_online(
                    text,result,report,online_request,keep_local=True,
                    online_interactive=online_interactive,
                    source_hint=source_hint,
                )
                report.elapsed_seconds=time.monotonic()-started
                _LOG.info(
                    "trace semantic candidates=%d online=%s elapsed=%.3f",
                    len(semantic_hits),report.online_queried,
                    report.elapsed_seconds,
                )
                return result,report
            result.level="none"  #none是五级判定的兜底档
            result.note=self._no_local_match_note()
            self._maybe_try_online(
                text,result,report,online_request,
                online_interactive=online_interactive,
                source_hint=source_hint,
            )
            report.elapsed_seconds=time.monotonic()-started
            _LOG.info(
                "trace none online=%s filtered_cross_language=%d "
                "filtered_low_relevance=%d elapsed=%.3f",
                report.online_queried,report.online_filtered_cross_language,
                report.online_filtered_low_relevance,report.elapsed_seconds,
            )
            return result,report

        #跨片段拼接
        #每块对齐比例最多只有50%
        span_match=self._best_span(text) #算出整段话在这篇文献里的整体覆盖率
        best=candidates[0]
        span_note=""  #span_note默认空串

        if span_match is not None and span_match.coverage>best.score+0.02:
            primary=max(span_match.parts,key=lambda p:p.query_coverage) #拼接覆盖率比单片得分高出0.02才采用拼接结果
            if primary.chunk_id!=best.chunk_id:  #主展示片段变了才需要回查原始候选
                refreshed=[c for c in candidates if c.chunk_id==primary.chunk_id]
                best=refreshed[0] if refreshed else primary
            effective_ratio=span_match.coverage  #比例改用拼接覆盖率
            report.spanned_chunks=len(span_match.parts)
            if len(span_match.parts)>1:
                span_note=("（这段话跨越了 %d 个正文片段，"
                             "已拼接后计算覆盖率 %.0f%%）"
                             %(len(span_match.parts),span_match.coverage*100))
        else:  #没跨片段就用单片段得分当比例
            effective_ratio=best.score

        #旁证：BM25 只记录诊断信息，不参与置信度计算
        bm25_support=self._check_bm25_support(text,best.chunk_id)
        report.bm25_support=bm25_support

        confidence=_confidence_from_ratio(
            effective_ratio,best.matched_tokens
        )

        #五级判定
        short_exact=(effective_ratio>=0.999 and best.matched_tokens>=SHORT_EXACT_LEVEL_TOKENS)
        if((effective_ratio>=CONFIDENCE_EXACT and best.matched_tokens>=MIN_TOKENS_FOR_EXACT)
                or short_exact):
            result.level="local_exact" #高度重合的原文
        elif effective_ratio>=CONFIDENCE_FUZZY:  #比例到了模糊线就算高度相似
            result.level="local_fuzzy" #高度相似段落
        else:
            result.level="local_weak" #仅有部分文本重合，不足以确认出处

        hit=self._to_search_hit(best,text)  #组装对外展示的命中对象
        result.hit=hit
        source_doc=hit.doc
        result.source_label=self._source_label(source_doc)  #出处标签交给独立方法
        result.citations=self._citations_for(source_doc)
        result.candidates=[self._to_search_hit(c,text) for c in candidates[1:]]
        seen_chunks={best.chunk_id,*[item.chunk_id for item in result.candidates]}
        vector_support=False
        vector_same_chunk=False
        vector_similarity=0.0
        for vector_hit in vector_hits:
            hit_doc_id=source_doc.id if source_doc is not None else ""
            vector_support=vector_support or vector_hit.doc_id==hit_doc_id
            vector_same_chunk=vector_same_chunk or vector_hit.chunk_id==best.chunk_id
            vector_similarity=max(vector_similarity,vector_hit.score)
            if vector_hit.chunk_id not in seen_chunks:
                result.candidates.append(self._vector_to_search_hit(vector_hit))
                seen_chunks.add(vector_hit.chunk_id)
        result.confidence=confidence
        result.decision_basis=DecisionBasis.LOCAL_FINGERPRINT.value
        result.supporting_signals=SupportSignals(
            bm25=bm25_support,
            vector=vector_support,
            same_chunk_id=vector_same_chunk if vector_support else True,
        ).to_dict()
        if vector_hits:
            result.supporting_signals["vector_similarity"]=round(
                vector_similarity,4
            )
        result.matched_text=best.matched_text
        result.found=confidence>=found_threshold and result.level in(
            "local_exact","local_fuzzy") #只有本地指纹证据及等级允许确认
        result.context_before,result.context_after=self.phrase.context(
            best.chunk_id,best.char_start or 0,best.char_end or 0
        )
        result.note=self._note_for_level(result.level,best,confidence,effective_ratio)  #说明文字按级别生成，不同级别用词不同
        if span_note:
            result.note=result.note+"\n"+span_note
        if vector_support and not vector_same_chunk:
            result.note=(
                result.note
                +"\n向量旁证指向同一文献的其他片段，但不改变原文覆盖率与置信度。"
            )
        elif vector_hits and not vector_support:
            result.note=(
                result.note
                +"\n向量召回指向其他文献，已作为独立语义候选展示，"
                "未并入当前命中置信度。"
            )

        #本地只有弱信号时也去联网问一次
        if not result.found:
            self._maybe_try_online(
                text,result,report,online_request,keep_local=True,
                online_interactive=online_interactive,
                source_hint=source_hint,
            )

        report.elapsed_seconds=time.monotonic()-started  #成功路径同样结算耗时
        _LOG.info(
            "trace result level=%s found=%s confidence=%.4f "
            "phrase_candidates=%d vector_candidates=%d online=%s elapsed=%.3f",
            result.level,result.found,result.confidence,
            report.phrase_candidates,report.vector_candidates,
            report.online_queried,report.elapsed_seconds,
        )
        return result,report

    def _vector_search(self,text:str)->List[VectorHit]:
        """按需执行本地向量召回；失败只记录诊断，不影响指纹链路。"""
        try:
            if self.vector is None:
                self.vector_encoder=get_embedding_encoder(
                    backend=self.settings.vector_backend,
                    model_dir=self.settings.vector_model_dir,
                    batch_size=self.settings.vector_batch_size,
                    dimension=self.settings.vector_dim,
                )
                self.vector_model=self.vector_encoder.model
                self.vector=VectorIndex(
                    self.store,
                    encoder=self.vector_encoder,
                    dimension=self.settings.vector_dim,
                    model=self.vector_model,
                )
            if self.store.count_chunk_vectors(model=self.vector_model)<=0:
                self._last_vector_error="向量索引尚未建立，请执行 cli.py index"
                return[]
            hits=self.vector.search(
                text,
                top_k=max(self.settings.vector_top_k,10),
                min_score=self.settings.vector_min_score,
            )
            if not hits:
                self._last_vector_error=""
            else:
                self._last_vector_error=""
            return hits
        except Exception as exc:  # noqa: BLE001
            self._last_vector_error="%s: %s"%(
                type(exc).__name__,str(exc)[:160]
            )
            return[]

    def _vector_to_search_hit(self,hit:VectorHit)->SearchHit:
        chunk=self.store.get_chunk(hit.chunk_id)
        doc=self.store.get_document(hit.doc_id,with_fulltext=False)
        return SearchHit(
            chunk_id=hit.chunk_id,
            doc_id=hit.doc_id,
            score=hit.score,
            text=hit.text or(chunk.text if chunk else ""),
            section=chunk.section if chunk else "body",
            heading=chunk.heading if chunk else None,
            page=chunk.page_start if chunk else None,
            doc=doc,
            sources=["vector"],
            ranks={"vector":1},
        )

    def _best_span(self,text:str)->Optional[Any]:
        """取跨片段覆盖率最高的那篇文献；失败时返回 None"""
        try:  #拼接搜索本身就复杂，包一层异常
            matches=self.phrase.search_spanned(text,top_k=3)
        except Exception:
            return None
        return matches[0] if matches else None  #没有结果返回None

    #旁证环节独立成段：指纹给主判据，BM25 只做交叉验证
    def _check_bm25_support(self,quote:str,chunk_id:str)->bool:
        """看 BM25 是否也把同一片段排进前列"""
        bm25_support=False
        try:  #延迟导入BM25Index
            from litagent.index.lexical import BM25Index

            index=BM25Index(self.store,k1=self.settings.bm25_k1,
                              b=self.settings.bm25_b,  #k1和b必须和建索引时一致
                              tokenizer_cache_dir=self.settings.cache_dir)
            top=[cid for cid,_ in index.search(quote,top_k=10)]
            bm25_support=chunk_id in top
        except Exception:  # noqa: BLE001
            pass
        return bm25_support

    #结果构造
    def _to_search_hit(self,phrase_hit:Any,quote:str)->SearchHit:
        """把一句短语命中的结果翻译成统一的溯源结果对象"""
        chunk=self.store.get_chunk(phrase_hit.chunk_id)
        doc:Optional[Document]=None
        if chunk is not None:
            doc=self.store.get_document(chunk.doc_id,with_fulltext=False)  #这里只要元数据不要全文：溯源展示用不上正文
        return SearchHit(
            chunk_id=phrase_hit.chunk_id,
            doc_id=chunk.doc_id if chunk else phrase_hit.doc_id,
            score=phrase_hit.score,
            text=phrase_hit.matched_text or(chunk.text if chunk else ""),  #命中片段自带原文就优先用它
            section=chunk.section if chunk else "body",
            heading=chunk.heading if chunk else None,
            page=chunk.page_start if chunk else None,
            doc=doc,
            sources=["phrase"],  #来源只写phrase
            ranks={"phrase":1},
        )

    @staticmethod
    def _note_for_level(level:str,hit:Any,confidence:float,
                        ratio:Optional[float]=None)->str:
        shown=hit.score if ratio is None else ratio  #有对齐比例就用它，没有才退回检索得分
        if level=="local_exact":
            return("在本地语料中找到高度重合的原文（覆盖率 %.0f%%），"
                    "可以据此标注引用。建议人工核对一次上下文。"
                    %(shown*100))
        if level=="local_fuzzy":
            return("本地语料中有高度相似的段落（覆盖率 %.0f%%），"  #本地相似：可能是改写或转引
                    "可能是改写、译文或转引。**引用前请务必核对原文**。"
                    %(shown*100))
        if level=="local_weak":
            return("仅有部分文本重合（覆盖率 %.0f%%），"  #本地弱命中：措辞要拦住用户
                    "不足以确认出处。**请勿直接据此引用**，"
                    "可尝试提供更长的原文片段。"%(shown*100))
        return ""

    def _no_local_match_note(self)->str:
        return("本地语料中没有找到任何文本重合的段落。\n"  #\n是提示与说明之间的硬换行，两个信息层级不同
                "提醒：只有已经收录进本地库的文献才可能被溯源到。"
                "如果这段话来自库里没有的论文，本地检索在原理上就不可能命中。")

    #联网兜底
    ONLINE_SKIP_TYPES=frozenset({  #用不可变集合装黑名单：这份名单是常量
        "peer-review","component","grant","journal-issue","journal-volume",
        "editorial","erratum","paratext",
    })
    #: 联网兜底要同时查询的数据源：四个源各发一次，谁回来算谁。
    #: 早期的实现是「第一个源拿到结果就不再问后面的」，实际上等于只查 OpenAlex，
    #: 这里把四个源并成一批并发查询，覆盖面才真正落到四个库上。
    ONLINE_SOURCES=("openalex","crossref","arxiv","semanticscholar")
    OPENCLI_SOURCE="opencli"
    #: 每个源最多取回的候选数
    ONLINE_PER_SOURCE=4
    #: 跨源去重后总共保留的候选数：界面与命令行都按这个规模渲染
    ONLINE_MAX_CANDIDATES=8
    #: 同语种联网候选的最低关键词覆盖率，避免泛词命中占满列表。
    ONLINE_MIN_RELEVANCE=0.35
    #: API 已返回足够候选时，不再等待慢浏览器源。
    ONLINE_EARLY_STOP_CANDIDATES=4
    #: 公平窗口（秒）：给四个源的这个时长内平等地等，健康的源基本都能在这个窗口内回来
    ONLINE_GRACE=3.0
    #: 整批等待上限（秒）。到点还没回来的源直接放弃，交互不能被慢源拖住
    ONLINE_TIMEOUT=8.0

    def _online_search_one(self,source_name:str,keywords:List[str],
                           topic_words:List[str])->Tuple[str,List[Document],str]:
        """单个源的联网检索，返回（源名, 文档列表, 错误串）；任何异常都不外抛。

            作为并发任务的最小单元，它必须自包含：自己建源、自己关源、
            自己把异常翻译成错误串。这样某个源挂掉只影响它自己那一格，
            其余三个源照常返回结果，不会出现一个源失败整批就没结果的情况。

            交互式溯源刻意关掉退避重试：Semantic Scholar 未配密钥时固定返回
            429，默认的 4 轮指数退避会把每次溯源拖长十几秒；这里改成一次不成就
            交出错误串，由整批超时和其余源补位。

            两个关键词入参分工不同：`keywords` 是按词长排的（长词信息量大，适合
            关键词检索），`topic_words` 是按原文顺序、去过停用词的（短语/主题更连贯）。
        """
        query=" ".join(keywords[:8])  #通用检索式：取前8个关键词，够表达主题又不至于长到被引擎截断
        if source_name=="arxiv":
            #arXiv 要用定制检索式，原因是它的默认包装会把多词查询拼成整句短语
            #（`all:"a b c"`），而短语是近似精确匹配：只要这句话不是原样出现在标题或
            #摘要里，就一条都查不到。实测拿按词长排出来的词去拼短语（如
            #`all:"molecular networks dominant"`）必然空手，所以这里换成按原文顺序的
            #主题词，并且不再拼短语。
            #这里改成布尔 OR：`all:a OR all:b OR all:c` 是 arXiv 官方支持的写法，
            #任意一个词命中就能召回，再由它自己的相关度排序把最贴题的排在前面。
            terms=topic_words or keywords[:3]  #主题词一个都没抽到时，退回长度序的前三个
            query=" OR ".join("all:%s"%kw for kw in terms[:3])
        try:
            from litagent.fetch.base import get_source

            source=get_source(source_name,self.settings)  #按名字和配置现取数据源实例
            try:
                source.client.max_retries=0  #交互路径不做退避重试：慢源交给并发和超时兜底
                source.client.timeout=min(source.client.timeout,self.ONLINE_TIMEOUT)
                return source_name,list(source.search(query,limit=self.ONLINE_PER_SOURCE)),""
            finally:
                source.close()  #放在finally里关
        except Exception as exc:  # noqa: BLE001
            from litagent.fetch.http import HttpError

            if isinstance(exc,HttpError):
                status=getattr(exc,"status",None)
                if status==429:
                    detail="HTTP 429 限流"
                elif status:
                    detail="HTTP %s"%status
                elif getattr(exc,"retryable",False):
                    detail="网络连接失败"
                else:
                    detail="请求失败"
            else:
                detail=type(exc).__name__
            return source_name,[],detail

    def _opencli_search(
        self,quote:str,*,interactive:bool=False
    )->Tuple[str,List[Document],str]:
        """OpenCLI 作为浏览器补充通道，使用完整查询和独立超时。"""
        try:
            from litagent.fetch.base import get_source

            source=get_source(self.OPENCLI_SOURCE,self.settings)
            try:
                if not hasattr(source,"search_with_errors"):
                    documents=list(source.search(
                        quote,limit=source.settings.opencli_max_candidates_per_site
                    ))
                    errors=[]
                else:
                    documents,errors=source.search_with_errors(
                        quote,
                        limit=source.settings.opencli_max_candidates_per_site,
                        interactive=interactive,
                    )
                return self.OPENCLI_SOURCE,documents,"；".join(errors)
            finally:
                source.close()
        except Exception as exc:  # noqa: BLE001
            return self.OPENCLI_SOURCE,[],"%s: %s"%(type(exc).__name__,str(exc)[:160])

    @staticmethod
    def _interleave(batches:List[List[Document]])->List[Document]:
        """把各源的结果按轮次交叉合并，让每个源都能轮到名额。

            直接首尾相接会把名额全给排在前面的源，交叉合并后每个源的第 1 篇
            都排在各自第 2 篇之前，截断时各源的占比自然均衡。
        """
        rows:List[Document]=[]
        longest=max((len(batch) for batch in batches),default=0)  #最长的那个结果决定外层循环次数
        for index in range(longest):
            for batch in batches:
                if index<len(batch):  #这一轮某个源没有第 index 篇就跳过它
                    rows.append(batch[index])
        return rows

    @staticmethod
    def _candidate_count(done:set)->int:
        """统计已完成任务给出的候选数；传进来的任务都已完成，取结果不会阻塞。"""
        total=0
        for future in done:
            try:
                total+=len(future.result()[1])
            except Exception:  # noqa: BLE001
                continue
        return total

    @staticmethod
    def _query_language(text:str)->str:
        return "zh" if has_cjk(text or "") else "en"

    @staticmethod
    def _document_language(doc:Document)->str:
        declared=str(doc.language or "").strip().lower()
        if declared.startswith(("zh","chi","zho")):
            return "zh"
        sample=" ".join([doc.title or "",doc.abstract or ""])
        if has_cjk(sample):
            return "zh"
        if declared.startswith(("en","eng")):
            return "en"
        return "en" if sample.strip() else "unknown"

    @classmethod
    def _matches_language(cls,doc:Document,language:str)->bool:
        doc_language=cls._document_language(doc)
        if doc_language=="unknown":
            return True
        return doc_language==language

    @staticmethod
    def _online_sources_for(query_language:str)->Tuple[str,...]:
        if query_language=="zh":
            # Crossref 的中文法学覆盖率很低，匿名交互请求也经常直接返回 429。
            # 中文查询先走 OpenAlex，中文站点由 OpenCLI 浏览器补充层负责。
            return("openalex",)
        return QuoteTracer.ONLINE_SOURCES

    @classmethod
    def _qualified_candidate_count(
        cls,done:set,futures:Dict[Any,str],language:str
    )->int:
        total=0
        for future in done:
            if futures.get(future) not in cls.ONLINE_SOURCES:
                continue
            try:
                documents=future.result()[1]
            except Exception:  # noqa: BLE001
                continue
            total+=sum(
                1 for doc in documents
                if cls._matches_language(doc,language)
            )
        return total

    def _hint_search(self,hint:str)->Tuple[str,List[Document],str]:
        """按用户提供的 DOI 直接补一条高可信题录候选。"""
        doi=normalize_doi(hint or "")
        if not doi:
            return "source-hint",[],"来源提示中未识别到 DOI"
        for source_name in("crossref","openalex"):
            try:
                from litagent.fetch.base import get_source

                source=get_source(source_name,self.settings)
                try:
                    source.client.timeout=min(source.client.timeout,self.ONLINE_TIMEOUT)
                    doc=source.by_doi(doi)
                finally:
                    source.close()
                if doc is not None:
                    doc.url=doc.url or "https://doi.org/%s"%doi
                    doc.extra["source_hint"]=True
                    return "source-hint",[doc],""
            except Exception:  # noqa: BLE001
                continue
        return "source-hint",[],"DOI %s 未解析到题录"%doi

    def _maybe_try_online(self,quote:str,result:TraceResult,
                          report:TraceReport,request:OnlineRequest,
                          keep_local:bool=False,*,
                          online_interactive:bool=False,
                          source_hint:str="")->None:
        """联网权限守卫的唯一入口。"""
        if not request.allow_online:
            return
        self._try_online(
            quote,result,report,request=request,keep_local=keep_local,
            online_interactive=online_interactive,
            source_hint=source_hint,
        )

    def _try_online(self,quote:str,result:TraceResult,
                    report:TraceReport,*,request:OnlineRequest,
                    keep_local:bool=False,online_interactive:bool=False,
                    source_hint:str="")->None:
        """按查询语言选择在线源，并只把同语种候选放入主列表。"""
        request.require_online()
        keywords=_extract_keywords(quote,limit=12)
        if not keywords:  #一个关键词都提不出来就早退
            return
        report.online_queried=True
        topic_words=tokenize(quote)[:3]
        query_language=self._query_language(quote)
        api_sources=self._online_sources_for(query_language)
        hint_kind,hint_value=parse_source_hint(source_hint)

        # DOI 可以独立解析；CNKI 文献标识用于给同一批 CNKI 搜索结果排序，
        # 不把带 invoice/nonce 的完整会话 URL 再发给任何来源。
        extra_jobs=1+(1 if hint_kind=="doi" else 0)
        pool=ThreadPoolExecutor(max_workers=len(api_sources)+extra_jobs,
                                thread_name_prefix="lit-online")
        try:
            futures={pool.submit(self._online_search_one,name,keywords,topic_words):name
                     for name in api_sources}
            futures[pool.submit(
                self._opencli_search,quote,interactive=online_interactive
            )]=self.OPENCLI_SOURCE
            if hint_kind=="doi":
                futures[pool.submit(self._hint_search,source_hint)]="source-hint"
            started=time.monotonic()
            done,pending=wait(futures,timeout=self.ONLINE_GRACE)
            if(pending and self._qualified_candidate_count(
                done,futures,query_language
            )<self.ONLINE_EARLY_STOP_CANDIDATES):
                api_pending={
                    future for future in pending
                    if futures[future] not in(
                        self.OPENCLI_SOURCE,"source-hint"
                    )
                }
                if api_pending:
                    elapsed=time.monotonic()-started
                    extra,_=wait(
                        api_pending,
                        timeout=max(0.0,self.ONLINE_TIMEOUT-elapsed),
                    )
                    done|=extra
                    pending-=extra
                if(self._qualified_candidate_count(
                    done,futures,query_language
                )<self.ONLINE_EARLY_STOP_CANDIDATES and pending):
                    elapsed=time.monotonic()-started
                    browser_timeout=(
                        self.settings.opencli_interactive_timeout+2.0
                        if online_interactive
                        else max(
                            self.ONLINE_TIMEOUT,
                            self.settings.opencli_timeout+1.5,
                        )
                    )
                    browser_pending={
                        future for future in pending
                        if futures[future]==self.OPENCLI_SOURCE
                    }
                    if browser_pending:
                        extra,_=wait(
                            browser_pending,
                            timeout=max(0.0,browser_timeout-elapsed),
                        )
                        done|=extra
                        pending-=extra
        finally:
            pool.shutdown(wait=False)  #不等超时的源收尾：它们这一轮的结果已经用不上了

        batches:List[List[Document]]=[]
        errors:List[str]=[]
        for future in futures:
            name=futures[future]
            if future not in done:  #到点还没回来的源直接放弃，只留一条错误说明
                future.cancel()  #没开跑的取消掉；已开跑的由它自己在后台收尾
                errors.append("%s: 超时未返回"%name)
                continue
            try:
                _,documents,error=future.result()
            except Exception as exc:  # noqa: BLE001
                errors.append("%s: %s"%(name,type(exc).__name__))
                continue
            if error:  #源自己报的错误（多半是限流或缺密钥）照样记下来，但不影响别的源
                errors.append("%s: %s"%(name,error))
            if documents:
                if name=="source-hint":
                    for doc in documents:
                        doc.extra["source_hint"]=True
                batches.append(documents)

        collected:List[Document]=[]  #最终交给结果的候选，已跨源去重并按上限截断
        seen_titles:set=set()
        filtered_cross_language=0
        filtered_low_relevance=0
        hint_matched=False
        for doc in self._interleave(batches):  #交叉合并后按序过筛，各源占比自然均衡
            doc_type=str((doc.extra or{}).get("type") or "").lower()  #extra可能没有也可能不是字典
            if doc_type in self.ONLINE_SKIP_TYPES:
                continue
            explicit_hint=bool((doc.extra or{}).get("source_hint"))
            if hint_kind=="cnki_filename":
                raw_hint=(
                    "%s %s"%(doc.url or "",(doc.extra or{}).get("opencli_raw") or "")
                )
                if hint_value.lower() in raw_hint.lower():
                    explicit_hint=True
                    hint_matched=True
                    doc.extra["source_hint"]=True
            if not explicit_hint and not self._matches_language(doc,query_language):
                filtered_cross_language+=1
                continue
            key=(doc.doi or "") or re.sub(r"\W+","",doc.title.lower())[:80]
            if not key or key in seen_titles:  #键为空说明标题也没实质内容，或者这篇已经收过
                continue
            seen_titles.add(key)  #同一篇被两个源同时抓到（DOI相同）时只保留先出现的那条
            doc.extra["online_language"]=self._document_language(doc)
            relevance=self._online_relevance(quote,doc)
            if not explicit_hint and relevance<self.ONLINE_MIN_RELEVANCE:
                filtered_low_relevance+=1
                continue
            doc.extra["online_relevance"]=relevance
            collected.append(doc)

        tier_order={"primary_api":0,"browser_supplement":1,"external_other":2}
        collected.sort(key=lambda doc:(
            0 if (doc.extra or{}).get("source_hint") else 1,
            tier_order.get(source_tier_for(doc.source).value,3),
            -float((doc.extra or{}).get("online_relevance") or 0.0),
            doc.source,
            doc.title,
        ))
        collected=collected[:self.ONLINE_MAX_CANDIDATES]

        report.online_error="；".join(errors[:4])  #多个源的错误合并成一行，个别源失败不再抹掉别的源的成功
        if hint_kind=="cnki_filename" and not hint_matched:
            hint_error=(
                "source-hint: 已识别 CNKI 文献标识，但本轮候选未包含该标识"
            )
            report.online_error="；".join(
                part for part in(report.online_error,hint_error) if part
            )
        report.online_filtered_cross_language=filtered_cross_language
        report.online_filtered_low_relevance=filtered_low_relevance

        if collected:
            result.online_candidates=[
                {
                    **SourceCandidate(
                    provider=doc.source,
                    source=doc.source,
                    source_tier=source_tier_for(doc.source),
                    document=doc,
                    ).to_public_dict(citation=self._safe_citation(doc)),
                    "language":(doc.extra or{}).get("online_language",""),
                    "relevance_score":(doc.extra or{}).get("online_relevance"),
                }
                for doc in collected
            ]
            if not keep_local and result.level in("none",""):
                result.level="online"  #只在本地本来就一无所获时改等级
            used:List[str]=[]  #统计真正给出候选的源名，按首次出现顺序排列
            for doc in collected:
                if doc.source not in used:
                    used.append(doc.source)
            addition=("已联网检索（%s），找到 %d 篇可能相关的文献。"
                        "**这些文献不在本地库中，无法确认这段话是否出自它们，"
                        "请自行核实。**"%("、".join(used),len(collected)))
            if filtered_cross_language:
                addition+=" 已过滤 %d 条与查询语种不一致的候选。"%filtered_cross_language
            if filtered_low_relevance:
                addition+=" 已过滤 %d 条弱相关候选。"%filtered_low_relevance
            result.note=(result.note+"\n"+addition).strip()  #追加在原有说明之后并去掉首尾空白
        elif filtered_cross_language or filtered_low_relevance:
            result.note=(
                result.note
                +"\n在线来源没有通过语种与相关度门槛的候选"
                "（跨语种 %d 条，弱相关 %d 条）。"
                %(filtered_cross_language,filtered_low_relevance)
            ).strip()
        if not collected and errors:
            result.note=(
                result.note
                +"\n联网来源状态："
                +"；".join(errors[:3])
            ).strip()

    @staticmethod
    def _online_relevance(quote:str,doc:Document)->float:
        """给已通过语种门槛的候选做轻量关键词相关度排序。"""
        query_tokens=set(tokenize(quote))
        if not query_tokens:
            return 0.0
        sample=" ".join([doc.title or "",doc.abstract or ""])
        document_tokens=set(tokenize(sample))
        if not document_tokens:
            return 0.0
        coverage=len(query_tokens&document_tokens)/float(len(query_tokens))
        title_tokens=set(tokenize(doc.title or ""))
        title_coverage=(
            len(query_tokens&title_tokens)/float(len(query_tokens))
            if title_tokens else 0.0
        )
        return round(min(1.0,0.7*coverage+0.3*title_coverage),4)

    @staticmethod
    def _safe_citation(doc:Document)->str:
        """格式化引用；出错时返回空串，绝不因此中断溯源。"""
        try:
            if(doc.extra or{}).get("kind")=="guji":  #古籍和论文走两套体例
                from litagent.cite.guji import format_guji_all

                formats=format_guji_all(doc.to_citation_dict())
                return formats.get("gbt7714") or formats.get("history") or ""
            from litagent.cite.gbt7714 import format_gbt7714

            return format_gbt7714(doc.to_citation_dict())
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _citations_for(doc:Optional[Document])->Dict[str,str]:
        """给出该文献所有可用的引用格式。"""
        if doc is None:  #None直接返回空字典
            return{}
        try:
            if(doc.extra or{}).get("kind")=="guji":
                from litagent.cite.guji import format_guji_all

                return{k:v for k,v in format_guji_all(doc.to_citation_dict()).items()  #isinstance加非空双重过滤
                        if isinstance(v,str) and v}
            from litagent.cite.gbt7714 import format_gbt7714

            citation=format_gbt7714(doc.to_citation_dict())  #论文只需要国标格式，页下注那套是历史学专用
            return{"gbt7714":citation} if citation else{}
        except Exception:
            return{}

    @staticmethod
    def _source_label(doc:Optional[Document])->str:
        """出处串：古籍给 《史记》卷七《项羽本纪》，论文给 作者 (年份)。"""
        if doc is None:
            return ""  #None返回空串
        try:
            if(doc.extra or{}).get("kind")=="guji":
                from litagent.cite.guji import describe_source

                return describe_source(doc.to_citation_dict())  #卷次篇名只有古籍模块拼得出来
        except Exception:
            pass
        return doc.short_ref()

    #便捷包装
    def trace_simple(self,quote:str,**kwargs:Any)->TraceResult:
        """只要结果、不要诊断报告的简版接口。"""
        return self.trace(quote,**kwargs)[0]  #取第0项，诊断报告由TraceReport单独承载

    def explain(self,result:TraceResult)->str:
        """把溯源结果渲染成可读的文本（CLI 与界面共用）"""
        lines:List[str]=[]
        if not result.found:
            lines.append("【未能确认出处】")
            if result.note:
                lines.append(result.note)
            if result.level=="local_semantic" and result.hit is not None:
                doc=result.hit.doc
                lines.append("")
                lines.append("本地语义候选（非原文证据）：")
                lines.append(
                    "  - %s（相似度 %.3f）"
                    %(
                        doc.short_ref() if doc is not None else result.hit.chunk_id,
                        result.hit.score,
                    )
                )
            if result.candidates:  #本地候选和联网候选之间垫一个空行
                lines.append("")
                lines.append("本地候选（供参考，置信度不足）：")
                for hit in result.candidates[:3]:
                    lines.append("  - %s"%(hit.doc.short_ref() if hit.doc else hit.chunk_id))  #文献对象缺失时退回片段编号
            if result.online_candidates:
                lines.append("")
                lines.append("联网候选（本地库未收录）：")
                for item in result.online_candidates[:5]:  #联网最多列5条：这些本来就没法确认
                    venue=item.get("venue") or ""
                    head="  - %s (%s)"%(item.get("title","")[:70],
                                            item.get("year") or "年份未知")
                    if venue:  #出版信息有才拼上去，没有就留空
                        head+=" "+venue[:60]
                    lines.append(head)
                    if item.get("citation"):
                        lines.append("      "+item["citation"])  #引用串单独缩进一层
            return "\n".join(lines)

        hit=result.hit
        doc=hit.doc if hit else None
        lines.append("【找到出处】置信度 %.0f%%（%s）"  #等级名用括号附在百分数后面
                     %(result.confidence*100,result.level))
        lines.append("")
        if result.source_label:
            lines.append("  出处：%s"%result.source_label)
        if doc is not None and(doc.extra or{}).get("kind")!="guji":  #古籍不显示通用标题字段：它的标题是卷篇名
            lines.append("  文献：%s"%doc.title)
            if doc.authors:
                lines.append("  作者：%s"%", ".join(a.display for a in doc.authors[:6]))
            lines.append("  出处：%s %s"  #出版物缺省时给中文占位，年份缺省就留空
                         %(doc.venue or "(未知出版物)",doc.year or ""))
            if doc.doi:
                lines.append("  DOI ：%s"%doc.doi)
        if hit is not None and(doc is None or(doc.extra or{}).get("kind")!="guji"):  #古籍不显示内部段落名：它的出处已经由出处串精确给出
            location=hit.section
            if hit.page:
                location+="·PDF 第 %d 页"%hit.page  #带上PDF页码
            lines.append("  位置：%s"%location)
        lines.append("")
        if result.context_before or result.context_after:
            lines.append("  上下文：…%s【%s】%s…"  #截断是必须的：上下文原样输出会把界面撑爆
                         %(result.context_before[-80:],result.matched_text[:200],
                            result.context_after[:80]))

        if result.citations:  #有可用引用才排这一段，没有就整段略过
            lines.append("")
            history=result.citations.get("history")
            gbt=result.citations.get("gbt7714")
            if history:
                lines.append("  历史学注释体例（页下注，首次引用）：")
                lines.append("    "+history)
                short=result.citations.get("history_short")
                if short and short!=history:  #两份内容一致时不重复显示
                    lines.append("  再次引用：")
                    lines.append("    "+short)
            if gbt:
                lines.append("  GB/T 7714-2015：")
                lines.append("    "+gbt)

        #多个出处是常态而非例外
        others=[h for h in result.candidates
                  if h.doc is not None and(doc is None or h.doc.id!=doc.id)]
        if others:
            lines.append("")
            lines.append("  另见（同一段文字在其他文献里也出现，请自行判断该引哪一处）：")  #标题里明确写请自行判断
            for other in others[:3]:
                other_doc=other.doc
                if other_doc is None:
                    continue
                label=self._source_label(other_doc) or other_doc.short_ref()
                lines.append("    - %s"%label)
                try:
                    citations=self._citations_for(other_doc)  #这里再兜一层异常，前面的出处串已经拿到了
                except Exception:
                    citations={}
                if citations.get("history"):
                    lines.append("      "+citations["history"])
        if result.note:  #提示放在最末尾，它是对整份结果的补充说明
            lines.append("")
            lines.append("  提示："+result.note.replace("\n","\n        "))
        return "\n".join(lines)

__all__=[
    "QuoteTracer",
    "TraceReport",
    "DEFAULT_FOUND_THRESHOLD",
    "CONFIDENCE_EXACT",
    "CONFIDENCE_FUZZY",
    "CONFIDENCE_WEAK",
]
