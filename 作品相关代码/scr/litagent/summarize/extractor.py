"""从模型返回的 JSON 中整理文献总结和可引用论断。"""
from __future__ import annotations

import json
import re
from typing import Any,Dict,List,Optional,Sequence

from litagent.config import Settings,settings as default_settings
from litagent.index.store import Store
from litagent.llm.client import LLMClient,LLMError,extract_json
from litagent.schema import Chunk,Claim,Document,Summary,content_hash,utcnow
from litagent.summarize.prompts import(
    PROMPT_VERSION,  #提示词版本号，进了缓存键
    build_claims_messages,
    build_summary_messages,
)

MAX_CLAIM_SEGMENTS=8

CLAIM_SECTION_PRIORITY=(
    "abstract","conclusion","results","experiment","method",
    "contributions","discussion","introduction",
)  #元组顺序就是优先级：越靠前越先被挑中

def _clean_field(value:Any)->str:
    """把模型返回的字段规整成字符串"""
    if value is None:  #模型漏填或填成null，一律当空字符串
        return ""
    if isinstance(value,str):
        return value.strip()
    if isinstance(value,(list,tuple)):  #列表/元组按中文分号拼接，空的元素直接丢
        return "；".join(str(v).strip() for v in value if str(v).strip())
    if isinstance(value,dict):
        return "；".join("%s：%s"%(k,v) for k,v in value.items())
    return str(value).strip()  #数字、布尔等标量统一str()兜底

def _clean_keywords(value:Any,*,limit:int=12)->List[str]:
    if isinstance(value,str):
        parts=re.split(r"[,，;；、\s]+",value)
    elif isinstance(value,(list,tuple)):
        parts=[str(v) for v in value]
    else:
        return []

    result:List[str]=[]
    seen:set[str]=set()
    for part in parts:
        keyword=part.strip()
        if not keyword or keyword in seen:
            continue
        seen.add(keyword)
        result.append(keyword)
        if len(result)>=limit:
            break
    return result

def parse_summary(doc_id:str,payload:Any,*,model:str="",
                  content_digest:str="")->Summary:
    """把模型返回的 JSON 解析成 :class:'Summary'"""
    if not isinstance(payload,dict):  #不是字典，说明模型没按格式返回
        if isinstance(payload,list) and payload:
            payload=payload[0] if isinstance(payload[0],dict) else{}
        else:
            payload={"tldr":_clean_field(payload)}

    claims:List[Claim]=[]
    raw_claims=payload.get("claims")
    if isinstance(raw_claims,(list,tuple)):  #类型不对就当没有论断
        for item in raw_claims:
            claim=Claim.from_dict(item)
            if claim.text and len(claim.text)>5:
                claims.append(claim)

    return Summary(
        doc_id=doc_id,
        tldr=_clean_field(payload.get("tldr") or payload.get("summary")),
        problem=_clean_field(payload.get("problem")),
        methods=_clean_field(payload.get("methods") or payload.get("method")),  #单复数都接，模型一会儿写一个样
        findings=_clean_field(payload.get("findings") or payload.get("results")),
        contributions=_clean_field(payload.get("contributions")),
        limitations=_clean_field(payload.get("limitations")),
        keywords=_clean_keywords(payload.get("keywords")),  #关键词单独走清洗+去重+截断
        claims=claims,
        model=model,
        prompt_version=PROMPT_VERSION,
        content_hash=content_digest,
        created_at=utcnow(),  #用UTC而不是本地时间
    )

def select_claim_segments(chunks:Sequence[Chunk],*,
                          limit:int=MAX_CLAIM_SEGMENTS)->List[Chunk]:
    """挑选用于论断抽取的片段"""
    if not chunks:  #一条片段都没有，早退，免得下面白排一遍序
        return[]

    def priority(chunk:Chunk)->int:
        try:
            return CLAIM_SECTION_PRIORITY.index(chunk.section)
        except ValueError:
            return len(CLAIM_SECTION_PRIORITY)

    ordered=sorted(chunks,key=lambda c:(priority(c),-c.token_count))
    selected:List[Chunk]=[]
    used_sections:Dict[str,int]={}
    for chunk in ordered:
        if used_sections.get(chunk.section,0)>=2:  #本节配额用完，跳过
            continue
        selected.append(chunk)
        used_sections[chunk.section]=used_sections.get(chunk.section,0)+1
        if len(selected)>=limit:  #够数就立刻返回
            return selected
    for chunk in ordered:
        if chunk in selected:  #第一轮已经选过的不重复选
            continue
        selected.append(chunk)
        if len(selected)>=limit:
            break
    return selected

class SummaryExtractor:
    """调用 LLM 抽取结构化总结"""

    def __init__(self,store:Store,settings:Optional[Settings]=None,
                 client:Optional[LLMClient]=None)->None:
        self.store=store
        self.settings=settings or default_settings  #没传配置就用全局单例
        self._client=client
        self._cached_calls=0  #客户端尚未构造时先记账，拿到客户端后再合并进用量

    @property
    def client(self)->LLMClient:
        if self._client is None:  #惰性构造：没配密钥的机器上
            self.settings.require_llm()
            self._client=LLMClient(self.settings,store=self.store)
            self._client.usage.cached_calls+=self._cached_calls
            self._cached_calls=0
        return self._client

    def _record_cache_hit(self)->None:
        """缓存命中不需要构造客户端；有客户端时再计入它的用量统计。"""
        if self._client is not None:
            self._client.usage.cached_calls+=1
        else:
            self._cached_calls+=1

    def extract(self,doc:Document,*,with_claims:bool=True,
                use_cache:bool=True)->Summary:
        """对一篇文献做结构化总结"""
        content=self._content_of(doc)
        digest=content_hash(
            doc.title,content[:200000],PROMPT_VERSION,self.settings.llm_model
        )  #只取前20万字符算指纹：提示词本来也会截断
        cache_key="summary:%s:%s"%(doc.id,digest)

        if use_cache:
            cached=self.store.cache_get(cache_key,self.settings.llm_model)  #缓存按模型隔离
            if cached:
                payload=extract_json(cached)
                if payload is not None:
                    summary=parse_summary(doc.id,payload,
                                            model=self.settings.llm_model,
                                            content_digest=digest)
                    self._record_cache_hit()
                    return summary

        messages=build_summary_messages(
            doc.title,doc.year,doc.venue,content,
            max_chars=self.settings.llm_max_input_chars,  #超长正文由它负责保留头尾地截断
        )
        payload=self.client.chat_json(messages)
        if payload is None:
            raise LLMError("模型未能返回可解析的 JSON（文献 %s）"%doc.id)

        summary=parse_summary(doc.id,payload,model=self.settings.llm_model,
                                content_digest=digest)

        if with_claims:  #论断是第二次独立调用
            extra_claims=self.extract_claims(doc,use_cache=use_cache)
            existing={c.text for c in summary.claims}
            for claim in extra_claims:
                if claim.text not in existing:  #两次调用可能摘出同一句，只保留先来的那条
                    summary.claims.append(claim)
                    existing.add(claim.text)

        if use_cache:
            self.store.cache_put(cache_key,
                                 json.dumps(summary.to_dict(),ensure_ascii=False),
                                 self.settings.llm_model)
        return summary

    def _content_of(self,doc:Document)->str:
        """取用于总结的正文：优先全文，否则退回摘要"""
        fulltext=doc.fulltext or self.store.get_fulltext(doc.id) or ""  #内存里没有就去库里捞，两级都空才认命
        if fulltext.strip():
            return fulltext
        return(doc.abstract or doc.title or "").strip()

    def extract_claims(self,doc:Document,*,
                       use_cache:bool=True)->List[Claim]:
        """从正文片段里摘出可直接引用的论断"""
        chunks=list(self.store.iter_chunks(doc.id))  #iter_chunks是生成器，先转成列表
        if not chunks:
            return[]
        selected=select_claim_segments(chunks)
        segments=[
            {
                "index":index,
                "section":chunk.section,
                "page":chunk.page_start,
                "text":chunk.text[:2200],  #单片段截到2200字符
            }
            for index,chunk in enumerate(selected)
        ]
        if not segments:  #理论上不会发生（上面已保证非空）
            return[]

        digest=content_hash(
            doc.id,"|".join(s["text"] for s in segments),
            PROMPT_VERSION,"claims",self.settings.llm_model,
        )
        cache_key="claims:%s:%s"%(doc.id,digest)
        if use_cache:
            cached=self.store.cache_get(cache_key,self.settings.llm_model)
            if cached:
                payload=extract_json(cached)
                if payload is not None:
                    self._record_cache_hit()
                    return self._claims_from_payload(payload,selected)

        payload=self.client.chat_json(build_claims_messages(segments))
        if payload is None:  #论断是附加产物，拿不到就返回空表
            return[]
        if use_cache:
            self.store.cache_put(cache_key,
                                 json.dumps(payload,ensure_ascii=False),  #存原始payload而不是解析后的Claim
                                 self.settings.llm_model)
        return self._claims_from_payload(payload,selected)

    @staticmethod
    def _claims_from_payload(payload:Any,selected:Sequence[Chunk])->List[Claim]:
        """解析论断JSON，并用chunk_index补全章节与页码"""
        if not isinstance(payload,dict):  #模型返回数组或裸文本，一律当空结果
            return[]
        items=payload.get("claims")
        if not isinstance(items,(list,tuple)):
            return[]

        claims:List[Claim]=[]
        for item in items:
            claim=Claim.from_dict(item)  #字符串形态也能接住
            if not claim.text or len(claim.text)<6:
                continue
            if isinstance(item,dict):  #只有字典形态才带chunk_index
                index=item.get("chunk_index")
                if isinstance(index,int) and 0<=index<len(selected):
                    chunk=selected[index]
                    claim.section=claim.section or chunk.section  #模型自己填了就尊重它，空的才用反查值补
                    claim.page=claim.page or chunk.page_start
            claims.append(claim)
        return claims

    def close(self)->None:
        if self._client is not None:
            self._client.close()

__all__=[
    "SummaryExtractor",
    "parse_summary",
    "select_claim_segments",
    "MAX_CLAIM_SEGMENTS",
]
