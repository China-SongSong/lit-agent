"""用精确 n-gram 指纹查找引文所在的片段。"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from litagent.hashing import fingerprint_tokens
from litagent.schema import Chunk

#三个正则加三处分节线
_LATIN_RE=re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-]*")  #拉丁词元的边界：必须以字母或数字开头
_CJK_RE=re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
_NUMBER_RE=re.compile(r"\d+(?:\.\d+)?")  #数字独立成词元

def exact_tokens(text:str)->List[str]:
    """确定性分词，返回词元列表"""
    return [token for token, _, _ in exact_tokens_with_spans(text)]

def exact_tokens_with_spans(text:str)->List[Tuple[str,int,int]]:
    """确定性分词，同时返回每个词元在原串中的字符区间"""
    if not text:
        return []

    spans:List[Tuple[str,int,int]]=[]
    number_spans:List[Tuple[int,int]]=[]

    for match in _NUMBER_RE.finditer(text):
        spans.append((match.group(0),match.start(),match.end()))
        number_spans.append((match.start(),match.end()))

    for match in _LATIN_RE.finditer(text):
        start,end=match.start(),match.end()
        if any(start>=left and end<=right for left,right in number_spans):
            continue
        token=match.group(0).lower().strip("-'")
        if token:
            spans.append((token,start,end))

    for match in _CJK_RE.finditer(text):
        spans.append((match.group(0),match.start(),match.end()))

    spans.sort(key=lambda item:item[1])
    return spans

def normalized_exact_text(text:str)->str:
    """返回仅由 exact_tokens 和分隔符组成的稳定匹配文本。"""
    return "\x1f".join(exact_tokens(text))


def _token_char_span(
    text:str,
    normalized_query:str,
)->Optional[Tuple[int,int,str]]:
    """在原文中定位规范化词元串并映射回字符区间。"""
    spans=exact_tokens_with_spans(text)
    if not spans or not normalized_query:
        return None
    tokens=[token for token,_,_ in spans]
    joined="\x1f".join(tokens)
    pos=joined.find(normalized_query)
    while pos>=0:
        end_pos=pos+len(normalized_query)
        left_ok=pos==0 or joined[pos-1]=="\x1f"
        right_ok=end_pos==len(joined) or joined[end_pos]=="\x1f"
        if left_ok and right_ok:
            break
        pos=joined.find(normalized_query,pos+1)
    if pos<0:
        return None
    start_index=joined[:pos].count("\x1f") if pos else 0
    matched_count=normalized_query.count("\x1f")+1
    end_index=start_index+matched_count-1
    if start_index>=len(spans) or end_index< start_index or end_index>=len(spans):
        return None
    char_start=spans[start_index][1]
    char_end=spans[end_index][2]
    return char_start,char_end,text[char_start:char_end]


def _best_contiguous_match(
    query_tokens:Sequence[str],
    chunk_tokens:Sequence[str],
)->Tuple[int,int,int]:
    """动态规划求最长连续词元公共段：返回长度、查询起点、片段起点。"""
    if not query_tokens or not chunk_tokens:
        return 0,0,0
    previous=[0]*(len(chunk_tokens)+1)
    best_length=0
    best_query=0
    best_chunk=0
    for query_index,query_token in enumerate(query_tokens):
        current=[0]*(len(chunk_tokens)+1)
        for chunk_index,chunk_token in enumerate(chunk_tokens):
            if query_token!=chunk_token:
                continue
            length=previous[chunk_index]+1
            current[chunk_index+1]=length
            if length>best_length:
                best_length=length
                best_query=query_index-length+1
                best_chunk=chunk_index-length+1
        previous=current
    return best_length,best_query,best_chunk

#结果结构
@dataclass
class PhraseHit:
    """一次精确匹配的结果"""

    chunk_id:str  #片段id是必填，其余字段全给默认值
    doc_id:str=""
    score:float=0.0
    matched_tokens:int=0
    total_tokens:int=0
    chunk_token_offset:int=0  #片段侧的词元起点，即票数最高的那个差值
    query_token_offset:int=0
    query_token_start:int=0
    query_token_end:int=0
    char_start:Optional[int]=None  #字符区间是可选的：不取正文时压根算不出来
    char_end:Optional[int]=None
    matched_text:str=""

    @property
    def query_coverage(self)->int:
        """本片段覆盖的查询词元数"""
        return max(0,self.query_token_end-self.query_token_start)  #用闭开区间相减得到覆盖词元数

    def to_dict(self)->Dict[str,Any]:
        return{
            "chunk_id":self.chunk_id,
            "doc_id":self.doc_id,
            "score":round(self.score,4),
            "matched_tokens":self.matched_tokens,
            "total_tokens":self.total_tokens,
            "chunk_token_offset":self.chunk_token_offset,
            "query_token_start":self.query_token_start,
            "query_token_end":self.query_token_end,
            "char_start":self.char_start,
            "char_end":self.char_end,
            "matched_text":self.matched_text,
        }

@dataclass
class SpannedMatch:
    """跨片段的拼接匹配结果"""

    doc_id:str
    coverage:float  #覆盖比例按查询词元算，分母是查询总长
    covered_tokens:int
    total_tokens:int
    parts:List[PhraseHit]=field(default_factory=list)

    @property
    def chunk_ids(self)->List[str]:
        return[part.chunk_id for part in self.parts]  #拼接过哪几个片段要能反查

    def to_dict(self)->Dict[str,Any]:
        return{
            "doc_id":self.doc_id,
            "coverage":round(self.coverage,4),
            "covered_tokens":self.covered_tokens,
            "total_tokens":self.total_tokens,
            "chunks":self.chunk_ids,
        }

class PhraseIndex:
    """精确n-gram指纹索引"""

    #默认值7：既能扛住中文里常见的四字词加虚词
    DEFAULT_N=7

    def __init__(self,store:Any,*,n:Optional[int]=None)->None:
        self.store=store
        self.n_from_index=self._read_n_from_store()  #三个来源的优先级：显式传参最高，其次是索引元数据
        self.n=n or self.n_from_index or self.DEFAULT_N

    def _read_n_from_store(self)->Optional[int]:
        """从索引元数据读取建索引时使用的n"""
        try:
            raw=self.store.get_meta("fingerprint_n")  #取元数据包在try里：老库可能压根没有meta表
        except Exception:
            return None
        if not raw:
            return None
        try:
            value=int(raw)  #存进去的是字符串，int转换失败说明元数据被写坏了
        except(TypeError,ValueError):
            return None
        return value if value>=3 else None

    def describe(self)->Dict[str,Any]:
        """诊断信息：本索引实际用的是什么参数"""
        return{
            "n":self.n,
            "n_from_index_meta":self.n_from_index,
            "fingerprints":self.store.count_fingerprints(),  #n_from_index_meta和n分开报
        }

    def fingerprints_of(self,text:str)->List[int]:
        """计算文本的指纹序列，位置即词元位置"""
        return fingerprint_tokens(exact_tokens(text),self.n)  #复用hashing里的滑动窗口，窗口长度传实例的n

    def build(self,chunks:Sequence[Chunk])->Dict[str,List[int]]:
        """为一组片段计算指纹，返回片段id到指纹列表的字典"""
        return{chunk.id:self.fingerprints_of(chunk.text) for chunk in chunks}  #字典推导式一次算完全部片段，顺序无所谓

    def search(self,query:str,*,top_k:int=10,
               min_ratio:float=0.25,
               with_text:bool=True)->List[PhraseHit]:
        """在语料里找与query精确重合的片段"""
        tokens=exact_tokens(query)  #查询侧和索引侧走同一个分词函数
        total=len(tokens)

        #撞上成千上万个片段，结果毫无意义
        if total<3:
            return[]

        #索引是按固定 n 建的；查询比 n 短时不能换成更小的 n 去查，
        #否则算出来的是 3~6 元指纹，和索引里的 7 元指纹永远对不上。
        if total<self.n:
            return self._search_short(tokens,query=query,top_k=top_k,min_ratio=min_ratio,
                                      with_text=with_text)
        n=self.n

        query_fps=fingerprint_tokens(tokens,n)  #用索引实际使用的 n 计算查询指纹
        if not query_fps:
            return[]

        lookup=self.store.fingerprint_lookup(query_fps)  #一次批量查库把所有指纹的落点全取回来
        if not lookup:
            return[]

        votes:Dict[str,List[Tuple[int,int]]]=defaultdict(list)
        for query_pos,fingerprint in enumerate(query_fps):
            for chunk_id,chunk_pos in lookup.get(fingerprint,()):
                votes[chunk_id].append((query_pos,chunk_pos))

        hits:List[PhraseHit]=[]
        for chunk_id,pairs in votes.items():
            offsets=Counter(chunk_pos-query_pos for query_pos,chunk_pos in pairs)
            best_offset,best_count=offsets.most_common(1)[0]
            ratio=best_count/float(len(query_fps))  #票数除以查询指纹总数，而不是除以该片段的总命中数
            if ratio<min_ratio:
                continue

            aligned=[q for q,c in pairs if c-q==best_offset]  #只在最高票那条对角线上的数对才算数
            q_lo,q_hi=min(aligned),max(aligned)+n

            hit=PhraseHit(
                chunk_id=chunk_id,
                score=ratio,
                matched_tokens=best_count+n-1,  #命中词元数是窗口个数加n减1而不是乘n
                total_tokens=total,
                chunk_token_offset=best_offset,
                query_token_start=max(0,q_lo),
                query_token_end=min(total,q_hi),  #min夹一下右端：窗口右端可能越过查询末尾
            )

            if with_text:
                self._fill_location(hit,tokens,n,best_offset)  #不取正文时跳过定位，能省掉一次读正文和一次重分词
            hits.append(hit)

        hits.sort(key=lambda h:(-h.score,h.chunk_id))
        return hits[:max(1,int(top_k))]  #top_k兜底至少返回1条：调用方传0多半是没配好

    def _search_short(self,tokens:Sequence[str],*,query:str,top_k:int,
                      min_ratio:float,with_text:bool)->List[PhraseHit]:
        """短查询兜底：先用词元倒排找候选，再在候选片段里做连续精确对齐。"""
        total=len(tokens)
        raw=(query or "").strip()
        normalized_query=normalized_exact_text(raw)
        if normalized_query:
            #规范化词元串忽略空格、换行、标点和大小写差异；命中后只对候选
            #片段做一次原文定位，不再逐位置扩展搜索。
            rows=self.store.direct_normalized_matches(normalized_query)
            direct:List[PhraseHit]=[]
            for row in rows:
                chunk_id,text=row
                located=_token_char_span(text,normalized_query)
                if located is None:
                    continue
                char_start,char_end,matched_text=located
                hit=PhraseHit(
                    chunk_id=chunk_id,
                    score=1.0,
                    matched_tokens=total,
                    total_tokens=total,
                    query_token_start=0,
                    query_token_end=total,
                    char_start=char_start,
                    char_end=char_end,
                    matched_text=matched_text,
                )
                direct.append(hit)
            if direct:
                direct.sort(key=lambda h:h.chunk_id)
                return direct[:max(1,int(top_k))]

        # 末词在词中被截断时，完整词元串仍可作为候选预筛条件。
        # 先查“除末词外的规范化前缀”，避免把全库倒排候选逐条重新分词。
        if total>=2:
            prefix="\x1f".join(tokens[:-1])
            prefix_rows=(
                self.store.direct_normalized_matches(prefix)
                if prefix else []
            )
            if prefix_rows:
                prefix_ids={chunk_id for chunk_id,_ in prefix_rows}
                prefix_hits=self._scan_short_candidates(
                    prefix_ids,tokens=tokens,min_ratio=min_ratio,
                    with_text=with_text,
                )
                if prefix_hits:
                    prefix_hits.sort(key=lambda h:(-h.score,h.chunk_id))
                    return prefix_hits[:max(1,int(top_k))]

        postings=self.store.postings_for_terms(tokens,max_df_ratio=1.0)
        candidate_ids={chunk_id for chain in postings.values() for chunk_id,_ in chain}
        if not candidate_ids:
            #BM25 词表与精确词元表不完全相同；用最长词元做一次 SQL 预筛，
            #避免为了一个短句把全库所有片段重新分词。
            needle=max(tokens,key=len)
            candidate_ids={row["chunk_id"] for row in self.store.connect().execute(
                "SELECT chunk_id FROM chunk_text WHERE instr(text,?) > 0",
                (needle,),
            )}
        if not candidate_ids:
            return[]

        hits=self._scan_short_candidates(
            candidate_ids,tokens=tokens,min_ratio=min_ratio,
            with_text=with_text,
        )
        hits.sort(key=lambda h:(-h.score,h.chunk_id))
        return hits[:max(1,int(top_k))]

    def _scan_short_candidates(
        self,chunk_ids:set,*,tokens:Sequence[str],min_ratio:float,
        with_text:bool,
    )->List[PhraseHit]:
        """只对预筛后的候选做逐词元对齐。"""
        total=len(tokens)
        hits:List[PhraseHit]=[]
        for chunk_id in chunk_ids:
            text=self.store.get_chunk_text(chunk_id) or ""
            if not text:
                continue
            spans=exact_tokens_with_spans(text)
            if not spans:
                continue
            chunk_tokens=[token for token,_,_ in spans]
            best_len,best_q,best_c=_best_contiguous_match(
                tokens,chunk_tokens
            )
            if not best_len:
                continue
            ratio=best_len/float(total)
            if ratio<min_ratio:
                continue
            hit=PhraseHit(
                chunk_id=chunk_id,
                score=ratio,
                matched_tokens=best_len,
                total_tokens=total,
                chunk_token_offset=best_c,
                query_token_start=best_q,
                query_token_end=min(total,best_q+best_len),
            )
            if with_text:
                hit.char_start=spans[best_c][1]
                hit.char_end=spans[best_c+best_len-1][2]
                hit.matched_text=text[hit.char_start:hit.char_end]
            hits.append(hit)
        return hits

    def search_spanned(self,query:str,*,top_k:int=10,
                       min_ratio:float=0.1,
                       with_text:bool=True)->List[SpannedMatch]:
        """跨片段拼接检索，按文献聚合覆盖率"""
        tokens=exact_tokens(query)
        total=len(tokens)
        if total<3:
            return[]  #少于3个词元连窗口都凑不齐

        hits=self.search(query,top_k=max(top_k*6,40),min_ratio=min_ratio,
                           with_text=with_text)
        if not hits:
            return[]  #一条都没捞到时早退

        conn=self.store.connect()
        chunk_ids=[h.chunk_id for h in hits]
        doc_of:Dict[str,str]={}
        for start in range(0,len(chunk_ids),400):
            batch=chunk_ids[start:start+400]
            marks=",".join(["?"]*len(batch))  #占位符按这批的实际长度生成，用列表乘法拼
            for row in conn.execute(
                "SELECT id, doc_id FROM chunks WHERE id IN (%s)"%marks,batch
            ):
                doc_of[row["id"]]=row["doc_id"]  #结果按行装进字典
        for hit in hits:
            hit.doc_id=doc_of.get(hit.chunk_id,"")

        by_doc:Dict[str,List[PhraseHit]]=defaultdict(list)
        for hit in hits:  #只要归属文献非空的命中，归不了组就没法参与拼接
            if hit.doc_id:
                by_doc[hit.doc_id].append(hit)

        matches:List[SpannedMatch]=[]
        #间隙容忍度：它决定两段覆盖算不算连成一片
        #算到——但这显然不是内容缺失
        #再大就会把真正不相邻的内容也算成连续了
        gap_tolerance=max(1,self.n//3)

        for doc_id,doc_hits in by_doc.items():
            intervals=sorted(  #先按起点排序再扫一遍，这是标准的区间合并写法
                (h.query_token_start,h.query_token_end) for h in doc_hits
            )
            merged:List[List[int]]=[]
            for start,end in intervals:
                if merged and start-merged[-1][1]<=gap_tolerance:
                    merged[-1][1]=max(merged[-1][1],end)
                else:
                    merged.append([start,end])  #不在容忍范围内就自己起一段

            covered=sum(end-start for start,end in merged)
            #零散命中若干不相邻的小段
            longest=max((end-start for start,end in merged),default=0)
            coverage=longest/float(total)

            matches.append(SpannedMatch(
                doc_id=doc_id,
                coverage=coverage,
                covered_tokens=covered,  #covered_tokens照实报所有段之和
                total_tokens=total,
                parts=sorted(doc_hits,key=lambda h:h.query_token_start),
            ))

        matches.sort(key=lambda m:(-m.coverage,m.doc_id))
        return matches[:max(1,int(top_k))]

    def _fill_location(self,hit:PhraseHit,query_tokens:Sequence[str],n:int,
                       best_offset:int)->None:
        """把词元对齐结果换算成片段正文里的字符区间与匹配文本"""
        text=self.store.get_chunk_text(hit.chunk_id)  #读正文要去库里取，索引里只有指纹和元数据
        if not text:
            return
        spans=exact_tokens_with_spans(text)
        if not spans:  #正文分不出词元时没法定位，直接返回
            return

        #best_offset 是「片段词元位置 - 查询词元位置」，
        #当只有查询后半段命中时，还要把查询侧的起点加回来。
        start_index=max(0,best_offset+hit.query_token_start)
        end_index=min(len(spans)-1,best_offset+hit.query_token_end-1)  #query_token_end 是开区间
        if start_index>=len(spans) or end_index<start_index:
            return

        char_start=spans[start_index][1]  #取第start_index个词元的起点
        char_end=spans[end_index][2]
        hit.char_start=char_start
        hit.char_end=char_end
        hit.matched_text=text[char_start:char_end]  #用字符区间从正文里切片回显

    def context(self,chunk_id:str,char_start:int,char_end:int,*,
                window:int=160)->Tuple[str,str]:
        """返回命中位置前后的上下文，前文与后文"""
        text=self.store.get_chunk_text(chunk_id) or ""
        if not text:  #正文为空时早退，一步就返回空串对
            return "",""
        before=text[max(0,char_start-window):char_start]
        after=text[char_end:char_end+window]
        return before.strip(),after.strip()  #两端各自strip：窗口是从正文里硬切的

def build_fingerprints(chunks:Sequence[Chunk],*,n:int=7
                       )->Dict[str,List[int]]:
    """便捷函数：为一组片段建立指纹"""
    return{chunk.id:fingerprint_tokens(exact_tokens(chunk.text),n)  #字典推导式一次算完
            for chunk in chunks}


__all__=[  #显式声明导出名单
    "exact_tokens",
    "exact_tokens_with_spans",
    "normalized_exact_text",
    "PhraseIndex",
    "PhraseHit",
    "SpannedMatch",
    "build_fingerprints",
]
