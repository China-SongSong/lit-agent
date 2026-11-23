"""中英混排分词 + BM25 稀疏检索。"""
from __future__ import annotations

import logging
import math
import re
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from litagent.schema import Chunk, has_cjk

_jieba=None  #模块级变量，保存 jieba 模块本身
_jieba_ready=False

def _load_jieba(cache_dir:Optional[Path]=None)->Any:
    """加载 jieba，并把词典缓存指向项目内目录。"""
    global _jieba,_jieba_ready
    if _jieba_ready:
        return _jieba  #直接返回已缓存的模块
    try:
        with warnings.catch_warnings():  #进入临时上下文
            warnings.simplefilter("ignore",DeprecationWarning)
            import jieba

        jieba.setLogLevel(logging.WARNING)  #调低 jieba 日志级别，只保留警告以上
        if cache_dir is not None:
            try:
                cache_dir.mkdir(parents=True,exist_ok=True)
                jieba.dt.tmp_dir=str(cache_dir)
                jieba.dt.cache_file=str(cache_dir/"jieba.cache")
            except Exception:
                pass  #空实现，故意什么都不做，直接继续往下执行
        _jieba=jieba
    except ImportError:
        _jieba=None
    _jieba_ready=True  #无论成功还是失败都标记已尝试过
    return _jieba

ENGLISH_STOPWORDS=frozenset("""
a about above after again against all am an and any are aren't as at be because
been before being below between both but by can cannot could couldn't did didn't
do does doesn't doing don't down during each few for from further had hadn't has
hasn't have haven't having he her here hers herself him himself his how i if in
into is isn't it its itself let's me more most mustn't my myself no nor not of off
on once only or other ought our ours ourselves out over own same shan't she should
shouldn't so some such than that the their theirs them themselves then there these
they this those through to too under until up very was wasn't we were weren't what
when where which while who whom why with won't would wouldn't you your yours
yourself yourselves also however thus therefore moreover furthermore although
though whether using used use based proposed shown given due via et al
""".split())

CHINESE_STOPWORDS=frozenset("""
的 了 和 是 就 都 而 及 与 着 或 一个 没有 我们 你们 他们 它们 这个 那个 这些
那些 自己 什么 怎么 为什么 因为 所以 但是 然而 因此 并且 以及 对于 关于 通过
可以 能够 进行 具有 一些 一种 各种 不同 相同 主要 重要 研究 方法 结果 分析
本文 本文中 文中 其中 基于 由于 如果 那么 这样 那样 这样 那样 之 其 此
""".split())

ALL_STOPWORDS=ENGLISH_STOPWORDS|CHINESE_STOPWORDS  #用竖线运算符把两个集合合并成总停用词表

_LATIN_TOKEN_RE=re.compile(r"[a-z0-9][a-z0-9'\-\.]*",re.I)
_CJK_CHAR_RE=re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")  #用 Unicode 码位区间匹配中日韩文字

_PURE_DIGITS_RE=re.compile(r"^\d+$")

def tokenize(text:str,*,use_jieba:bool=True,  #定义分词函数，星号后的参数必须用关键字传入
             cache_dir:Optional[Path]=None,
             drop_stopwords:bool=True,
             min_latin_length:int=2)->List[str]:
    """把文本切成检索词元。"""
    if not text:  #空文本直接短路返回
        return[]

    tokens:List[str]=[]

    if use_jieba and has_cjk(text):  #只有开关打开并且文本确实含中文
        jieba=_load_jieba(cache_dir)
        if jieba is not None:
            for word in jieba.cut_for_search(text):
                word=word.strip()  #去掉词两端的空白
                if not word:
                    continue
                if _CJK_CHAR_RE.search(word):
                    if len(word)<=12:  #只保留不超过 12 个字的词
                        tokens.append(word.lower())
                elif _LATIN_TOKEN_RE.fullmatch(word):
                    lowered=word.lower()
                    if len(lowered)>=min_latin_length:  #只保留长度达标的英文词
                        tokens.append(lowered)
            if drop_stopwords:
                tokens=[t for t in tokens if t not in ALL_STOPWORDS]
            return tokens  #中文路径到此直接返回

    for match in _LATIN_TOKEN_RE.finditer(text):
        word=match.group(0).lower().strip(".-'")  #取匹配文本转小写
        if len(word)<min_latin_length:
            continue
        if _PURE_DIGITS_RE.match(word) and len(word)>4:
            continue  #跳过它；行尾注释说明超长数字串多半是噪声
        tokens.append(word)

    for char in text:
        if _CJK_CHAR_RE.match(char):
            tokens.append(char)

    if drop_stopwords:  #兜底路径同样要做一次停用词过滤
        tokens=[t for t in tokens if t not in ALL_STOPWORDS]
    return tokens

def term_frequencies(tokens:Sequence[str])->Dict[str,int]:
    """统计词频。"""
    counts:Dict[str,int]={}
    for token in tokens:
        counts[token]=counts.get(token,0)+1  #用 get 取旧值再加一
    return counts

class BM25Index:
    """基于 SQLite 倒排表的 BM25 检索器（Okapi BM25）。"""

    def __init__(self,store:Any,*,k1:Optional[float]=None,
                 b:Optional[float]=None,  #参数 b 控制文档长度归一化的强度
                 tokenizer_cache_dir:Optional[Path]=None,
                 use_jieba:bool=True)->None:
        self.store=store
        settings=getattr(store,"settings",None)  #用 getattr 安全取配置对象
        self.k1=k1 if k1 is not None else getattr(settings,"bm25_k1",1.5)
        self.b=b if b is not None else getattr(settings,"bm25_b",0.75)
        self.tokenizer_cache_dir=tokenizer_cache_dir
        self.use_jieba=use_jieba  #保存是否启用 jieba 分词的开关

        self._total_chunks:Optional[int]=None
        self._avg_length:Optional[float]=None

    @property  #用属性装饰器
    def total_chunks(self)->int:
        if self._total_chunks is None:
            self._total_chunks=self.store.count_chunks()
        return self._total_chunks

    @property  #同样用属性装饰器暴露平均文档长度
    def avg_length(self)->float:
        if self._avg_length is None:
            self._avg_length=self.store.avg_chunk_length()
        return self._avg_length or 1.0  #用 or 兜底

    def refresh(self)->None:
        """索引变更后清空统计量缓存。"""
        self._total_chunks=None  #把总数缓存置空，下次访问会重新查库
        self._avg_length=None

    def tokenize(self,text:str)->List[str]:
        return tokenize(text,use_jieba=self.use_jieba,
                        cache_dir=self.tokenizer_cache_dir)

    def search(self,query:str,*,top_k:int=20,
               max_df_ratio:float=0.6)->List[Tuple[str,float]]:
        """检索，返回 [(chunk_id, score)]（按分数降序）。"""
        terms=self.tokenize(query)
        if not terms:  #分词结果是空的
            return[]
        return self.search_by_terms(terms,top_k=top_k,max_df_ratio=max_df_ratio)

    def search_by_terms(self,terms:Sequence[str],*,top_k:int=20,
                        max_df_ratio:float=0.6)->List[Tuple[str,float]]:
        """直接以词元列表检索（供已分好词的调用方复用）。"""
        unique_terms=list(dict.fromkeys(t for t in terms if t))
        if not unique_terms:  #去重之后一个有效词都不剩，同样要提前返回
            return[]

        total=self.total_chunks
        if total==0:  #索引还是空的
            return[]

        postings=self.store.postings_for_terms(unique_terms,max_df_ratio=max_df_ratio)
        if not postings:  #一条候选都没有
            return[]

        candidate_ids={chunk_id for chain in postings.values()  #用集合推导收集所有倒排链里出现过的片段编号
                         for chunk_id,_ in chain}
        lengths=self._chunk_lengths(list(candidate_ids))

        avgdl=self.avg_length  #取平均文档长度 avgdl
        k1,b=self.k1,self.b
        scores:Dict[str,float]={}

        for term,chain in postings.items():  #外层循环遍历每个查询词与它对应的倒排链
            df=len(chain)
            idf=math.log(1.0+(total-df+0.5)/(df+0.5))
            if idf<=0:  #双保险：IDF 不为正就跳过这个词
                continue
            for chunk_id,tf in chain:
                dl=lengths.get(chunk_id) or avgdl
                denominator=tf+k1*(1.0-b+b*dl/avgdl)  #算分母：词频加上按文档长度算的惩罚项
                if denominator<=0:
                    continue
                scores[chunk_id]=scores.get(chunk_id,0.0)+(
                    idf*tf*(k1+1.0)/denominator  #这一项就是 BM25 单词得分
                )

        if not scores:
            return[]
        ranked=sorted(scores.items(),key=lambda kv:(-kv[1],kv[0]))
        return ranked[:max(1,int(top_k))]

    def _chunk_lengths(self,chunk_ids:Sequence[str])->Dict[str,int]:
        """批量取片段长度（token_count）。"""
        if not chunk_ids:
            return{}
        conn=self.store.connect()
        lengths:Dict[str,int]={}
        unique=list(dict.fromkeys(chunk_ids))
        for start in range(0,len(unique),400):
            batch=unique[start:start+400]  #切出当前这一批需要查询的编号
            marks=",".join(["?"]*len(batch))
            for row in conn.execute(
                "SELECT id, token_count FROM chunks WHERE id IN (%s)"%marks,batch
            ):  #这一行收尾查询调用
                lengths[row["id"]]=int(row["token_count"] or 0)
        return lengths

def build_term_frequencies(chunks:Sequence[Chunk],*,
                           use_jieba:bool=True,
                           cache_dir:Optional[Path]=None
                           )->Dict[str,Dict[str,int]]:
    """为一组片段建立 {chunk_id: {term: tf}} 倒排数据。"""
    result:Dict[str,Dict[str,int]]={}
    for chunk in chunks:
        tokens=tokenize(chunk.text,use_jieba=use_jieba,cache_dir=cache_dir)
        chunk.token_count=max(1,len(tokens))
        result[chunk.id]=term_frequencies(tokens)  #统计该片段的词频
    return result


__all__=[
    "tokenize",
    "term_frequencies",
    "BM25Index",
    "build_term_frequencies",
    "ALL_STOPWORDS",
    "ENGLISH_STOPWORDS",
    "CHINESE_STOPWORDS",
]
