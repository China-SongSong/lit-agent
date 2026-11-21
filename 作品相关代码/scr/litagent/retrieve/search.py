"""合并 BM25 和精确指纹的召回结果。"""
from __future__ import annotations

import time
from dataclasses import dataclass,field
from typing import Any,Dict,List,Optional,Sequence,Tuple

from litagent.config import Settings,settings as default_settings
from litagent.index.hybrid import fuse_rankings
from litagent.index.lexical import BM25Index
from litagent.index.phrase import PhraseIndex
from litagent.index.store import Store
from litagent.schema import Document,SearchHit

DEFAULT_WEIGHTS:Dict[str,float]={
    "phrase":1.6,
    "bm25":1.0,
}  #字典结束，两路默认权重到此定义完成；指纹是溯源核心，权重给得比 BM25 高

@dataclass
class SearchReport:
    """一次检索的元信息，用于解释为什么返回这些结果。"""

    query:str=""
    routes_used:List[str]=field(default_factory=list)  #字段：真正成功的召回路名列表
    route_counts:Dict[str,int]=field(default_factory=dict)
    route_errors:Dict[str,str]=field(default_factory=dict)
    candidates:int=0
    elapsed_seconds:float=0.0  #字段：本次检索花了多少秒，由单调时钟相减得到

    def summary(self)->str:
        if not self.routes_used:
            return "检索：无可用召回路径"  #一路都没成功时返回这句提示
        parts=["%s(%d)"%(name,self.route_counts.get(name,0))
                 for name in self.routes_used]
        return "检索：%s 路召回 %s -> 融合后 %d 个候选，耗时 %.2f 秒"%(
            len(self.routes_used)," + ".join(parts),  #传前两个参数：路数用列表长度算
            self.candidates,self.elapsed_seconds)

class HybridSearcher:
    """两路混合检索器：BM25 关键词召回 + 精确指纹召回。"""

    def __init__(self,store:Store,settings:Optional[Settings]=None)->None:
        self.store=store
        self.settings=settings or default_settings
        self.bm25=BM25Index(
            store,
            k1=self.settings.bm25_k1,  #传入词频饱和参数 k1，默认 1.5
            b=self.settings.bm25_b,
            tokenizer_cache_dir=self.settings.cache_dir,
        )
        self.phrase=PhraseIndex(store)  #构造指纹索引只传存储对象

    #注释分隔线，这一段小节标题到此结束
    def search(self,query:str,*,top_k:Optional[int]=None,
               routes:Sequence[str]=("phrase","bm25"),
               weights:Optional[Dict[str,float]]=None,
               candidate_pool:int=100,
               with_documents:bool=True)->Tuple[List[SearchHit],SearchReport]:
        """混合检索，返回 (结果列表, 报告)。"""
        started=time.monotonic()  #记录开始时间；用单调时钟而不是墙上时钟
        report=SearchReport(query=query)
        weights={**DEFAULT_WEIGHTS,**(weights or{})}

        rankings:List[List[Tuple[str,float]]]=[]
        used:List[str]=[]
        names:List[str]=[]
        route_weights:List[float]=[]

        if "phrase" in routes:
            try:  #开始异常保护，索引文件缺失或损坏都可能抛异常
                hits=self.phrase.search(query,top_k=candidate_pool,min_ratio=0.2,
                                          with_text=False)
                ranking=[(h.chunk_id,h.score) for h in hits]
                report.route_counts["phrase"]=len(ranking)
                rankings.append(ranking)
                names.append("phrase")
                route_weights.append(weights.get("phrase",1.0))
                used.append("phrase")  #走到这里说明前面都没抛异常
            except Exception as exc:
                report.route_errors["phrase"]="%s: %s"%(
                    type(exc).__name__,str(exc)[:150])

        if "bm25" in routes:
            try:  #同样套上异常保护
                ranking=self.bm25.search(query,top_k=candidate_pool)
                report.route_counts["bm25"]=len(ranking)
                rankings.append(ranking)
                names.append("bm25")  #追加路名 bm25
                route_weights.append(weights.get("bm25",1.0))
                used.append("bm25")
            except Exception as exc:
                report.route_errors["bm25"]="%s: %s"%(
                    type(exc).__name__,str(exc)[:150])

        report.routes_used=used
        if not rankings:
            report.elapsed_seconds=time.monotonic()-started  #即使空手而归也要记下耗时
            return[],report

        fused=fuse_rankings(  #调融合函数
            rankings,
            k=self.settings.rrf_k,
            weights=route_weights,
            source_names=names,  #传入路名列表
            top_k=top_k or self.settings.default_top_k,
        )
        report.candidates=len(fused)

        hits=self._build_hits(fused,with_documents=with_documents)
        report.elapsed_seconds=time.monotonic()-started
        return hits,report

    def _build_hits(self,fused:Sequence[Any],*,
                    with_documents:bool=True)->List[SearchHit]:
        """把融合结果补全成 :class:`SearchHit`（带正文、章节、文献）。"""
        if not fused:
            return[]

        chunk_ids=[item.key for item in fused]
        texts=self.store.get_chunk_texts(chunk_ids)

        conn=self.store.connect()  #拿到数据库连接
        meta:Dict[str,Dict[str,Any]]={}
        for start in range(0,len(chunk_ids),400):
            batch=chunk_ids[start:start+400]
            marks=",".join(["?"]*len(batch))
            for row in conn.execute(
                "SELECT id, doc_id, section, heading, page_start FROM chunks "
                "WHERE id IN (%s)"%marks,batch
            ):  #for 循环的收尾，冒号之后开始循环体
                meta[row["id"]]=dict(row)

        doc_ids=list({m["doc_id"] for m in meta.values() if m.get("doc_id")})
        documents:Dict[str,Document]=(
            self.store.get_documents(doc_ids,with_fulltext=False)
            if with_documents else{}
        )

        hits:List[SearchHit]=[]
        for item in fused:
            info=meta.get(item.key,{})
            hits.append(SearchHit(
                chunk_id=item.key,  #片段编号直接用融合结果的键
                doc_id=info.get("doc_id",""),
                score=item.score,
                text=texts.get(item.key,""),
                section=info.get("section","body"),  #章节名取不到就默认 body
                heading=info.get("heading"),
                page=info.get("page_start"),
                doc=documents.get(info.get("doc_id","")),
                sources=list(item.sources),  #命中了哪几路召回，复制成新列表
                ranks=dict(item.ranks),
            ))
        return hits

    #注释分隔线，标出下一个小节开始
    def search_documents(self,query:str,*,top_k:int=10,
                         **kwargs:Any)->Tuple[List[Tuple[Document,float,List[SearchHit]]],SearchReport]:  #用关键字收集参数把其余选项原样转给片段级检索
        """按文献聚合的检索：同一篇论文的多个片段合并，取最高分。"""
        hits,report=self.search(query,top_k=max(top_k*4,40),**kwargs)

        grouped:Dict[str,List[SearchHit]]={}
        order:List[str]=[]
        for hit in hits:
            if not hit.doc_id:
                continue  #跳过这一条继续下一条
            if hit.doc_id not in grouped:
                grouped[hit.doc_id]=[]
                order.append(hit.doc_id)
            grouped[hit.doc_id].append(hit)

        results:List[Tuple[Document,float,List[SearchHit]]]=[]
        for doc_id in order:
            doc_hits=sorted(grouped[doc_id],key=lambda h:-h.score)
            best=doc_hits[0]
            doc=best.doc or self.store.get_document(doc_id,with_fulltext=False)
            if doc is None:
                continue  #跳过这篇论文；理论不该发生

            boost=1.0+0.04*min(len(doc_hits)-1,5)  #算出加成系数，每多一个片段加百分之四
            score=best.score*boost
            results.append((doc,score,doc_hits))

        results.sort(key=lambda item:-item[1])
        return results[:top_k],report

    #注释分隔线，小节标题结束
    def refresh(self)->None:
        """索引内容变化后调用（重建索引、新增语料后）。"""
        self.bm25.refresh()
        #指纹索引每次检索都直接查库，没有需要失效的内存缓存，所以这里不用刷新

__all__=["HybridSearcher","SearchReport","DEFAULT_WEIGHTS"]
