"""基于题录、关键词和摘要的可解释学科标签规则。"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Iterable, List, Sequence, Tuple

TAGGER_VERSION="5"
GENERAL_DISCIPLINE="综合类"

# 关键词按中英文同义表达分组。一个文献可以同时属于多个交叉学科。
DISCIPLINE_KEYWORDS={ 
    "新闻传播学类":(
        "新闻","传播学","传播","媒介","媒体","舆论","记者","采访","报刊",
        "journalism","communication","media","news","public opinion","press",
    ),
    "社会学类":(
        "社会学","社会结构","社会分层","社会资本","社会网络","社会群体",
        "社区","家庭","人口","社会不平等","社会流动","social","sociology",
        "social class","social network","inequality","community","demography",
    ),
    "历史学类":(
        "历史学","历史","史学","史料","朝代","古代","近代史","现代史",
        "考古","文明史","history","historical","dynasty","archaeology",
        "ancient","modern history","historiography",
    ),
    "文学类":(
        "文学","小说","诗歌","散文","戏剧","叙事","修辞","文论","作家",
        "literary","novel","poetry","narrative","rhetoric",
    ),
    "哲学类":(
        "哲学","伦理","认识论","本体论","价值论","逻辑学","philosophy",
        "ethics","ontology","epistemology","logic",
    ),
    "法学类":(
        "法律","法学","法治","司法","宪法","民法","刑法","诉讼","权利",
        "law","legal","court","judicial","constitution","rights","legislation",
    ),
    "经济学类":(
        "经济","经济学","金融","市场","贸易","产业","财政","货币","投资",
        "economy","economics","finance","financial","market","trade","industry",
    ),
    "管理学类":(
        "管理学","管理","组织行为","组织","企业治理","治理","战略","运营",
        "人力资源","management","organization","governance","strategy",
        "operations","human resources",
    ),
    "教育学类":(
        "教育学","教育","教学","课程","教师","学生","高等教育","学习成效",
        "education","pedagogy","curriculum","teaching","student","teacher",
    ),
    "心理学类":(
        "心理学","心理","认知","情绪","人格","行为实验","mental","psychology",
        "cognition","cognitive","emotion","personality","behavioral",
    ),
    "政治学类":(
        "政治学","政治","政府","公共政策","政策","国际关系","外交","国家治理",
        "politics","political","government","public policy","international relations",
        "diplomacy",
    ),
    "图书情报与档案管理类":(
        "图书馆","情报学","情报","文献计量","引文分析","引文","知识图谱",
        "信息检索","档案学","档案","数字人文","library","information science",
        "bibliometric","citation","knowledge graph","information retrieval",
        "archive","archival","digital humanities","数据引用","数据集引用",
        "数据计量","data citation","dataset citation","data metrics",
    ),
    "计算机科学类":(
        "计算机科学","计算机","人工智能","机器学习","深度学习","神经网络",
        "算法","大模型","自然语言处理","数据挖掘","软件工程","爬虫","网络爬虫",
        "聚焦爬虫","分布式爬虫","增量爬虫","网页蜘蛛","蜘蛛程序",
        "网页抓取","网页索引","computer science",
        "computer","artificial intelligence","machine learning","deep learning",
        "neural network","algorithm","large language model","nlp","data mining",
        "software engineering","crawler","crawling","focused crawling",
        "distributed crawling","incremental crawling","web crawling",
        "web scraping","web spider","spider program",
    ),
    "数学类":(
        "数学","统计学","概率论","数理统计","代数","几何","优化理论",
        "mathematics","mathematical","statistics","probability","algebra",
        "geometry","optimization",
    ),
    "物理学类":(
        "物理学","物理","量子力学","量子计算","凝聚态","光学","粒子物理",
        "physics","physical","quantum","condensed matter","optics","particle",
    ),
    "化学类":(
        "化学","化学反应","分子","催化","材料化学","光谱","chemistry",
        "chemical","molecule","molecular","catalysis","spectroscopy",
    ),
    "生物学类":(
        "生物学","生物","细胞","基因","遗传","蛋白质","生态学","biology",
        "biological","cell","gene","genetic","protein","ecology",
    ),
    "医学类":(
        "医学","临床","疾病","诊断","治疗","患者","药物","公共卫生",
        "medicine","medical","clinical","disease","diagnosis","treatment",
        "patient","pharmacology","public health",
    ),
    "环境科学类":(
        "环境科学","环境","生态保护","污染","气候变化","碳排","environment",
        "environmental","pollution","climate change","carbon emission","sustainability",
    ),
    "工程技术类":(
        "工程技术","工程","机械","制造","材料","建筑","能源","自动化",
        "电池","锂离子电池","锂电池","锂电","蓄电池","储能","储能系统",
        "热失控","电动汽车",
        "engineering","mechanical","manufacturing","materials","construction",
        "energy","automation","battery","batteries","lithium-ion","li-ion",
        "energy storage","battery storage","thermal runaway","electric vehicle",
    ),
    "艺术学类":(
        "艺术学","艺术","音乐","美术","设计学","影视","舞蹈","绘画",
        "art","arts","music","design","film","dance","painting",
    ),
}


def _normalize(value:str)->str:
    return unicodedata.normalize("NFKC",value or "").lower()


@lru_cache(maxsize=512)
def _ascii_pattern(keyword:str)->re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])%s(?![a-z0-9])"%re.escape(keyword),re.I)


def _contains(text:str,keyword:str)->bool:
    if re.fullmatch(r"[a-z0-9][a-z0-9 ._+/-]*",keyword):
        return bool(_ascii_pattern(keyword).search(text))
    return keyword in text


def _joined(values:Iterable[object])->str:
    return " ".join(
        str(value).strip()
        for value in values
        if str(value or "").strip()
    )


def classify_disciplines_with_scores(
    title:str="",
    abstract:str="",
    keywords:Sequence[str]=(),
    venue:str="",
    fulltext:str="",
)->List[Tuple[str,float]]:
    """返回最多三个学科及可解释匹配分。"""
    fields=(
        (_normalize(_joined((title,venue))),3.0),
        (_normalize(_joined(keywords)),2.5),
        (_normalize(abstract or ""),1.0),
        (_normalize((fulltext or "")[:6000]),0.35),
    )
    scores:List[Tuple[str,float]]=[]
    for label,words in DISCIPLINE_KEYWORDS.items():
        score=0.0
        for text,weight in fields:
            if not text:
                continue
            matched=sum(1 for word in words if _contains(text,_normalize(word)))
            score+=weight*matched
        if score>0:
            scores.append((label,round(score,3)))
    scores.sort(key=lambda item:(-item[1],item[0]))
    if not scores or scores[0][1]<2.0:
        return[(GENERAL_DISCIPLINE,0.0)]
    top=scores[0][1]
    selected=[
        item for item in scores
        if item[1]>=max(2.0,top*0.4)
    ][:3]
    return selected or[(GENERAL_DISCIPLINE,0.0)]


def classify_disciplines(
    title:str="",
    abstract:str="",
    keywords:Sequence[str]=(),
    venue:str="",
    fulltext:str="",
)->List[str]:
    """返回用于展示和筛选的学科标签。"""
    return[
        label for label,_ in classify_disciplines_with_scores(
            title=title,abstract=abstract,keywords=keywords,
            venue=venue,fulltext=fulltext,
        )
    ]


__all__=[
    "DISCIPLINE_KEYWORDS",
    "GENERAL_DISCIPLINE",
    "TAGGER_VERSION",
    "classify_disciplines",
    "classify_disciplines_with_scores",
]
