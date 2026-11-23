"""用倒数排名融合合并多路检索结果。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

Ranking=Sequence[Tuple[str,float]]  #一路排名的类型别名：[(key, score)]

@dataclass
class FusedHit:
    """融合后的一条结果，保留了它来自哪几路、各自排第几"""

    key:str  #文档标识，多路命中靠它对齐成同一条
    score:float=0.0
    sources:List[str]=field(default_factory=list)
    ranks:Dict[str,int]=field(default_factory=dict)
    raw_scores:Dict[str,float]=field(default_factory=dict)  #各路自己的原始分，只用于解释和调试

    def to_dict(self)->Dict[str,Any]:
        """返回可序列化的结果字典"""
        return{  #字典推导式不能用在这里
            "key":self.key,
            "score":round(self.score,6),
            "sources":list(self.sources),
            "ranks":dict(self.ranks),  #同上，包成新字典
            "raw_scores":{k:round(v,6) for k,v in self.raw_scores.items()},
        }


def fuse_rankings(  #和rankings同名，加了溯源字段
    rankings:Sequence[Ranking],
    *,
    k:int=60,
    weights:Optional[Sequence[float]]=None,
    source_names:Optional[Sequence[str]]=None,
    top_k:Optional[int]=None,
)->List[FusedHit]:
    """融合多路排名，并保留每路的原始排名与分数（便于解释与调试）"""
    if not rankings:  #一路排名都没有，空手早退
        return[]
    if weights is None:
        weights=[1.0]*len(rankings)
    if len(weights)!=len(rankings):  #路数对不上会错配，直接拦下
        raise ValueError("weights 数量与排名路数不一致")
    names=list(source_names) if source_names else[
        "route%d"%i for i in range(len(rankings))
    ]
    if len(names)!=len(rankings):  #名字比路数少会漏记来源，这里也拦下
        raise ValueError("source_names 数量与排名路数不一致")

    k=max(1,int(k))
    fused:Dict[str,FusedHit]={}  #key->融合结果对象

    for ranking,weight,name in zip(rankings,weights,names):
        if weight<=0:
            continue
        for rank,(key,raw_score) in enumerate(ranking,start=1):  #名次从1数起
            if not key:
                continue
            hit=fused.get(key)
            if hit is None:  #第一次见到这个key才建对象
                hit=FusedHit(key=key)
                fused[key]=hit
            hit.score+=weight/(k+rank)
            hit.ranks[name]=rank  #记下本路给的名次，界面要显示『#3』
            hit.raw_scores[name]=float(raw_score)
            if name not in hit.sources:
                hit.sources.append(name)

    result=sorted(fused.values(),key=lambda h:(-h.score,h.key))
    if top_k is not None:
        result=result[:max(1,int(top_k))]
    return result


__all__=[
    "Ranking",  #导出排名类型别名，调用方照着它准备输入
    "FusedHit",
    "fuse_rankings",
]
