"""合并重复文献。

先用 DOI 判断，再比较归一化标题；前两项都没有结果时，才用 SimHash
检查正文是否近似。这个顺序可以减少同名论文被错误合并的机会。
"""
from __future__ import annotations

from dataclasses import dataclass, field  #'field'要给可变默认值配'default_factory'
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple  #类型提示专用

from litagent.hashing import (  #SimHash由'hashing'统一提供，别在这里重写一份
    is_near_duplicate,
    simhash_text,
)
from litagent.schema import (  #归一化也走'schema'，两处口径必须一致
    Document,
    normalize_doi,
    normalize_title,
    title_similarity,
)

#: 标题判重阈值
TITLE_THRESHOLD=0.92   #0.92是卡出来的：再低会把『同一课题的两篇论文』误合并（误判），再高对不了预印本改标题（漏判）
#: SimHash 汉明距离阈值
SIMHASH_THRESHOLD=3   #取3是'is_near_duplicate'的默认值：距离放宽到5以上，不同论文也会被判成同一篇


@dataclass
class DuplicateGroup:
    """一组被判为重复的文献。"""

    keeper:Document   #留用的那条主记录
    duplicates:List[Document]=field(default_factory=list)   #被合并掉的那几条，默认工厂保证每个实例各有新列表
    reason:str=""   #命中时给的理由：'DOI'/'标题'/'正文近似'，报告里要按它分类计数

    def __len__(self)->int:
        return len(self.duplicates)+1   #加1是把主记录也算进去：本组一共几条


@dataclass
class DedupeReport:
    """去重结果报告。"""

    input_count:int=0   #进来的条数，由'dedupe_documents'在过滤完之后填
    output_count:int=0   #出去的条数，循环结束才填
    groups:List[DuplicateGroup]=field(default_factory=list)   #每组一份证据，出问题时能回头查是哪一条判错了

    @property
    def removed(self)->int:
        return self.input_count-self.output_count   #合并掉的条数：两者相减即可，不用另外维护计数器

    def summary(self)->str:
        by_reason:Dict[str,int]={}
        for group in self.groups:
            by_reason[group.reason]=by_reason.get(group.reason,0)+len(group.duplicates)   #get取不到返回0，第一次碰到这个理由也能直接累加
        lines=[
            "去重报告：输入 %d 篇 -> 输出 %d 篇（合并掉 %d 篇）"
            %(self.input_count,self.output_count,self.removed),
        ]   #'%'老式格式化，不用f-string，免得报告文本里的花括号要转义
        if by_reason:
            for reason,count in sorted(by_reason.items(),key=lambda kv:-kv[1]):
                lines.append("  按%s判重：%d 篇"%(reason,count))
        return "\n".join(lines)   #拼成一个多行字符串，调用方直接打印


def _content_score(doc:Document)->Tuple[int,int,int,int]:
    """信息量评分，用于在重复组里挑主记录。

    权重顺序（从高到低）：

    1. **有全文**——全文是溯源与总结的基础，有全文的记录价值远高于只有摘要的
    2. **ID 是 DOI 型**——``doi:10.xxxx/yyy`` 比 ``local:E:\\...`` 稳定得多，
       文件移动后仍能对上
    3. 有摘要
    4. 被引数

    必须挑信息量最大的那条当主记录：合并时其余记录的字段是**并入主记录**的，
    主记录本身字段少一点还能靠合并补齐，但主记录没全文、而重复记录有全文时，
    若选错了主记录，之后再有人用 ``store.get_document(id)`` 取到的就是贫瘠版本。
    """
    return(
        1 if(doc.fulltext or "").strip() else 0,   #第1位：有全文记1分，'or ""'兜住None，'strip'挡掉只有空白的假全文
        1 if(doc.id or "").startswith("doi:") else 0,   #第2位：DOI型ID记1分，本地路径型ID文件一挪就对不上了
        1 if(doc.abstract or "").strip() else 0,   #第3位：有摘要记1分
        doc.citation_count or 0,   #第4位：被引数直接当分，取不到时'None'用0兜底
    )   #元组按位比较，左边权重高：第1位分出高下时后面几位根本不参与比较


def _absorb(anchor:Document,incoming:Document)->Document:
    """把 ``incoming`` 并入 ``anchor``，返回合并后的对象。

    若 ``incoming`` 信息量更大，则以它为主体（这样主记录携带的是更完整的那份
    内容与更规范的 ID），再把 ``anchor`` 的字段补进去。
    """
    if _content_score(incoming)>_content_score(anchor):   #严格大于才换主体：打平时保持原样，免得来回抖动
        incoming.merge(anchor)   #新来的更强，让它当主体，把老记录并进去
        return incoming
    anchor.merge(incoming)   #否则老记录当主体，把新来的并进来
    return anchor


def dedupe_documents(documents:Iterable[Document],*,
                     title_threshold:float=TITLE_THRESHOLD,
                     use_simhash:bool=True,
                     simhash_threshold:int=SIMHASH_THRESHOLD)->Tuple[List[Document],DedupeReport]:
    """对一批文献去重，返回 ``(去重后的列表, 报告)``。

    **输出的位置保持输入顺序**（主记录出现在它首次出现的位置），
    这样结果可复现；但主记录**未必是首次出现的那条**——见 :func:`_content_score`。
    """
    docs=[d for d in documents if d is not None and not d.is_empty()]   #空壳和None提前丢弃：不参与去重，也不占输入计数
    report=DedupeReport(input_count=len(docs))   #输入计数按过滤后的算，否则『合并掉几篇』会对不上

    by_doi:Dict[str,int]={}        # doi -> 结果列表下标   #下标而不是对象：换主记录后位置不变，引用不会失效
    by_title:Dict[str,int]={}      # title_norm -> 结果列表下标   #键存归一化后的标题，比较时不用再归一化一次
    result:List[Document]=[]   #已确认不重复的记录，顺序即最终输出顺序
    groups:List[DuplicateGroup]=[]   #攒证据，最后整份挂到报告上

    def record_duplicate(index:int,incoming:Document,reason:str)->None:
        """合并到下标 ``index`` 处，并刷新去重映射。"""
        before=result[index]
        merged=_absorb(before,incoming)   #谁的信息量大谁当主体，主记录可能因此换人
        result[index]=merged
        # 主记录可能换成了 incoming，映射要跟着更新，否则同批次后续记录
        # 会匹配到已被替换掉的旧对象上
        if merged is not before:   #用'is'比对象身份，不是比内容相等
            for key,value in list(by_doi.items()):
                if value==index:
                    del by_doi[key]   #清掉指向旧主记录的DOI键
            for key,value in list(by_title.items()):   #标题表同理，两张表都要扫，少扫一张就会有残留
                if value==index:
                    del by_title[key]
        norm_doi=normalize_doi(merged.doi)   #换成新主体的DOI重新登记，不能沿用旧的
        if norm_doi:
            by_doi[norm_doi]=index   #空DOI不登记：否则一堆无DOI的记录会互相顶掉键
        norm_title=normalize_title(merged.title)
        if norm_title:
            by_title[norm_title]=index   #空标题同理不登记
        consumed=[before] if merged is not before else [incoming]
        groups.append(DuplicateGroup(keeper=merged,duplicates=consumed,reason=reason))

    for doc in docs:
        norm_doi=normalize_doi(doc.doi)
        norm_title=normalize_title(doc.title)

        # ---- 判据 1：DOI ----
        if norm_doi and norm_doi in by_doi:   #先判归一化后非空再查表：空DOI永远不会命中，能省掉一次字典查询
            record_duplicate(by_doi[norm_doi],doc,"DOI")
            continue

        # ---- 判据 2：标题精确 / 模糊 ----
        if norm_title:
            if norm_title in by_title:   #精确命中放在模糊之前：查表是O(1)，模糊比对要遍历，先走便宜的那条路
                record_duplicate(by_title[norm_title],doc,"标题")
                continue
            matched=_fuzzy_title_match(norm_title,by_title,title_threshold)
            if matched is not None:   #用'is not None'判空：下标0是合法值，写'if matched'会把第0条漏掉
                record_duplicate(by_title[matched],doc,"标题")
                continue

        # ---- 判据 3：SimHash（正文近似）----
        if use_simhash:   #开关留给测试和调试：关掉就只有DOI+标题两级
            text=doc.fulltext or doc.abstract or ""   #优先拿全文，没有全文退到摘要，都没有就是空串
            if len(text)>=200:   #太短的文本算SimHash没有意义：几十个字里改几个词，指纹照样撞上
                fingerprint=simhash_text(text)   #这里每条都比一次，不缓存，因为下面还要给已有记录现算
                matched=_simhash_match(fingerprint,result,simhash_threshold)
                if matched is not None:   #同样不能用真假判断，下标0要放行
                    record_duplicate(matched,doc,"正文近似")
                    continue

        # ---- 新记录 ----
        result.append(doc)   #三级判据都没命中才留下，追加到末尾即保持输入顺序
        position=len(result)-1   #下标=追加后的长度减1，先算出来给两张表用
        if norm_doi:
            by_doi[norm_doi]=position   #登记DOI
        if norm_title:
            by_title[norm_title]=position   #登记标题

    report.output_count=len(result)
    report.groups=groups
    return result,report   #元组解包给调用方：列表在前，报告在后


def _fuzzy_title_match(norm_title:str,by_title:Dict[str,int],
                       threshold:float)->Optional[str]:
    """在已有标题里找相似度超阈值的。

    用前缀粗筛把比对范围从 O(n) 降到接近 O(1)——否则一万篇语料做全量
    两两比对会非常慢。
    """
    prefix=norm_title[:6] if len(norm_title)>=6 else norm_title   #不足6字符就整个当前缀：切不出6个字符时切片会静默给短的，不如显式兜底
    if not prefix:
        return None   #空标题没得比，直接返回None
    for existing,_ in by_title.items():   #只用键（已有标题），值（下标）在这里用不着，用'_'占位
        if not existing.startswith(prefix):
            continue   #前缀对不上直接跳过，一次字符串比较就挡住绝大多数候选
        if title_similarity(norm_title,existing)>=threshold:   #粗筛过了才做真正的相似度计算，这步贵
            return existing
    return None   #一个都没命中就返回None，表示没有可合并的对象


def _simhash_match(fingerprint:int,result:Sequence[Document],
                   threshold:int)->Optional[int]:
    """在已有文献正文指纹里找近似重复，返回其下标。"""
    if fingerprint==0:
        return None   #指纹为0说明没有有效词元（'simhash64'对空输入就返回0），跟谁比都会误判，直接放行
    for index,existing in enumerate(result):   #一边遍历一边拿下标，返回的正是下标
        text=existing.fulltext or existing.abstract or ""   #取值口径必须和上面算指纹时一模一样，否则同一个对象两次算出不同指纹
        if len(text)<200:
            continue   #已有记录太短就跳过：和上面'>= 200'的门槛成对，两边不一致会出现单向可比
        existing_fingerprint=simhash_text(text)   #现算现用，不走缓存：批内去重只跑一遍，缓存反而占内存
        if is_near_duplicate(fingerprint,existing_fingerprint,threshold=threshold):   #汉明距离不大于阈值就判重
            return index
    return None   #整批比完都没撞上，说明这是新记录


def dedupe_against_store(store:Any,documents:Sequence[Document])->List[Document]:  # type: ignore[reportUndefinedVariable]
    """只保留库中尚不存在的文献（入库前的快速过滤）。

    与 :func:`dedupe_documents` 的区别：这里查的是**已有语料**，用于增量抓取
    时避免重复入库；而 :func:`dedupe_documents` 处理的是**本批次内部**的重复。
    """
    fresh:List[Document]=[]   #留下的新文献，顺序原样保持
    for doc in documents:
        norm_doi=normalize_doi(doc.doi)   #归一化后再查，不然'https://doi.org/10.x'和'10.x'会被当成两篇
        if norm_doi and store.find_by_doi(norm_doi) is not None:
            continue   #库里已有同DOI，丢弃
        if store.find_by_title(doc.title) is not None:
            continue   #DOI查不到再用标题兜一道：有的抓取结果只有标题
        fresh.append(doc)   #两道都没命中才放行
    return fresh


__all__=[   #声明'from litagent.clean.dedupe import *'时要导入哪些名字
    "DuplicateGroup",   #导出重复组结构，调用方可自行读组内明细
    "DedupeReport",   #导出报告类，便于单独构造与断言
    "dedupe_documents",   #导出批内去重主函数
    "dedupe_against_store",   #导出对库去重函数
    "TITLE_THRESHOLD",   #导出标题阈值，测试里要按它造边界用例
    "SIMHASH_THRESHOLD",   #导出SimHash阈值，同上
]
