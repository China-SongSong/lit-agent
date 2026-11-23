"""文献、片段、总结和检索结果共用的数据类。

正文与 chunks 由清洗流程回填。
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

VENUE_TYPES=(  #用元组而不是列表：类型集合是常量，元组不可变
    "journal",
    "conference",
    "book",
    "chapter",  #书中的一章
    "thesis",
    "report",
    "standard",
    "patent",  #专利文献，标识为P
    "newspaper",
    "web",
    "dataset",
    "preprint",  #预印本，如arXiv
    "unknown",
)

#键是匹配片段、值是标准章节名
SECTION_ALIASES:"Dict[str,str]"={
    "abstract":"abstract",
    "摘要":"abstract",
    "introduction":"introduction",
    "引言":"introduction",
    "绪论":"introduction",
    "related work":"related_work",
    "background":"related_work",
    "相关工作":"related_work",
    "研究现状":"related_work",
    "method":"method",
    "methods":"method",
    "methodology":"method",
    "approach":"method",
    "material":"method",
    "方法":"method",
    "实验方法":"method",
    "experiment":"experiment",
    "experiments":"experiment",
    "evaluation":"experiment",
    "result":"results",
    "results":"results",
    "discussion":"discussion",
    "结果":"results",
    "讨论":"discussion",
    "实验结果":"results",  #实验结果：归到results而不是method
    "conclusion":"conclusion",
    "conclusions":"conclusion",
    "结论":"conclusion",
    "总结":"conclusion",
    "limitation":"limitation",
    "limitations":"limitation",
    "局限":"limitation",
    "不足":"limitation",
    "contribution":"contributions",
    "contributions":"contributions",
    "主要贡献":"contributions",
    "acknowledg":"acknowledgement",
    "acknowledgment":"acknowledgement",
    "acknowledgments":"acknowledgement",
    "acknowledgements":"acknowledgement",
    "致谢":"acknowledgement",
    "keyword":"keywords",
    "keywords":"keywords",
    "关键词":"keywords",
    "reference":"references",
    "references":"references",
    "bibliography":"references",
    "参考文献":"references",
    "appendix":"appendix",
    "appendices":"appendix",
    "附录":"appendix",
}

#这张表只用来定位正文尾部参考文献段的起点
REFERENCE_HEADINGS=(
    "references",
    "reference",
    "bibliography",  #书目，语义等同参考文献
    "works cited",
    "literature cited",
    "参考文献",
    "引用文献",  #标题，两种都得拦下
)

_CJK_RE=re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_WS_RE=re.compile(r"[ \t\u00a0\u3000]+")
_MULTI_NL_RE=re.compile(r"\n{3,}")
_COMPOUND_SURNAMES=frozenset({
    "欧阳","司马","上官","诸葛","东方","皇甫","尉迟","公孙","慕容","司徒",
    "司空","夏侯","长孙","宇文","令狐","端木","轩辕","独孤","南宫","西门",
    "百里","呼延","东郭","南门","第五","梁丘","左丘","东门","申屠","太史",
    "公冶","宗政","濮阳","淳于","单于","太叔","仲孙","叔孙","公孟","公西",
    "漆雕","乐正","壤驷","公良","拓跋","夹谷","宰父","谷梁","段干","归海",
    "羊舌","微生","东野","南荣","北宫","司寇","司农","司士","司城","司鸿",
    "公仪","公乘","公皙","公输","公仲","公上","公门","公山","公坚","公伯",
    "公祖","公羊","公玉",
})

def _clean_person_text(value:Any)->str:
    """去除作者字段里偶发的 HTML 标签和实体，避免污染引用格式。"""
    text=html.unescape(str(value or ""))
    text=re.sub(r"<[^>]+>"," ",text)
    return normalize_whitespace(text)

def split_cjk_name(value:str)->tuple[str,str]:
    """拆分中文姓名，优先识别欧阳、司马等复姓。"""
    compact=re.sub(r"[\s·・]+","",str(value or ""))
    if not compact:
        return "", ""
    for surname in sorted(_COMPOUND_SURNAMES,key=len,reverse=True):
        if compact.startswith(surname) and len(compact)>len(surname):
            return surname,compact[len(surname):]
    if len(compact)>=2:
        return compact[0],compact[1:]
    return compact,""

def utcnow()->str:
    """返回当前UTC时间的字符串"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def has_cjk(text:str)->bool:
    """文本中是否含中日韩统一表意文字"""
    return bool(_CJK_RE.search(text or ""))  #用text or 空串兜住None

def normalize_whitespace(text:str)->str:
    """压缩多余空白字符，把任意脏文本压成干净文本，所有需要落盘或比对的字符串都先过一遍这里"""
    if not text:
        return ""  #空文本直接早退，省掉后面三条正则的编译与扫描开销
    text=text.replace("\r\n","\n").replace("\r","\n")
    text=_WS_RE.sub(" ",text)
    text=_MULTI_NL_RE.sub("\n\n",text)
    return text.strip()

def content_hash(*parts:Optional[str])->str:
    """计算sha1前16位十六进制作为指纹内容"""
    digest=hashlib.sha1()  #分段喂而不是先拼成一个大串
    for part in parts:
        digest.update((part or "").encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()[:16]  #取前16位十六进制，长度够用又短

def normalize_doi(doi:Optional[str])->Optional[str]:
    """把各种写法的DOI统一成标准形式，不带前缀、不带空格、不带尾部标点"""
    if not doi:  #空值早退返回None，统一按假值判断
        return None
    value=doi.strip()
    value=re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/","",value,flags=re.I)
    value=re.sub(r"^doi:\s*","",value,flags=re.I)  #去掉doi:前缀
    value=value.strip().rstrip(".,;)]}>\"'")
    value=value.strip().lower()
    if not value or not value.startswith("10."):
        return None  #条件成立时直接返回None
    return value

def normalize_title(title:Optional[str])->str:  #标题比对的唯一入口
    """把标题归一化成便于比对的串：去掉重音符号、HTML标签、非字母数字字符，转小写"""
    if not title:
        return ""
    text=unicodedata.normalize("NFKD",title)  ##NFKD分解把带重音字母拆成基字母加组合符
    text="".join(ch for ch in text if not unicodedata.combining(ch))
    text=text.lower()
    text=re.sub(r"<[^>]+>"," ",text)
    text=re.sub(r"[^\w\u3400-\u4dbf\u4e00-\u9fff]+","",text)  #只保留字母数字和汉字
    return text

def title_similarity(a:Optional[str],b:Optional[str])->float:
    """算出两个标题的相似度，返回0.0~1.0之间的浮点数"""
    na,nb=normalize_title(a),normalize_title(b)  #两边都先过一遍归一化
    if not na or not nb:
        return 0.0
    if na==nb:
        return 1.0
    if len(na)<3 or len(nb)<3:  #太短的串做三元组切分没有意义
        return 1.0 if na==nb else 0.0
    ga={na[i:i+3] for i in range(len(na)-2)}
    gb={nb[i:i+3] for i in range(len(nb)-2)}
    if not ga or not gb:  #任意一边切不出三元组就退化为不相似
        return 0.0
    return len(ga&gb)/float(len(ga|gb))


@dataclass
class Author:
    """单个著者：'family'为姓，'given'为名，'raw'保留原始串兜底"""
    family:str=""
    given:str=""
    raw:str=""

    @classmethod
    def parse(cls,value:Any)->"Author":
        """从各种形态的作者表示解析出 Author"""
        if isinstance(value,Author): 
            return value
        if isinstance(value,dict):
            family=_clean_person_text(value.get("family") or value.get("familyName"))
            given=_clean_person_text(value.get("given") or value.get("givenName"))
            raw=_clean_person_text(value.get("raw") or value.get("name"))
            if family or given:
                return cls(family=family,given=given,raw=raw or _join_name(family,given))
            if raw:
                return cls.parse(raw)
            return cls()
        text=_clean_person_text(value)
        if not text:
            return cls()
        #中文姓名：默认首字为姓，其余为名
        if has_cjk(text):
            family,given=split_cjk_name(text)
            return cls(family=family,given=given,raw=text)

        if "," in text:
            family,_,given=text.partition(",")
            return cls(family=family.strip(),given=given.strip(),raw=text)
        tokens=text.split()
        if len(tokens)==1:
            return cls(family=tokens[0],given="",raw=text)  #全部当成姓，名为空

        last=tokens[-1]
        #带缩写点："Kanamori H."
        bare=last.rstrip(".")
        is_initial=(
            last.endswith(".")
            or len(bare)==1  #去掉点后只有1个字符
            or(bare.isupper() and len(bare)<=3
                and len(tokens)>=2 and tokens[-2].rstrip(".").isupper())
        )
        if is_initial:
            return cls(family=" ".join(tokens[:-1]),given=last,raw=text)

        return cls(family=last,given=" ".join(tokens[:-1]),raw=text)

    @property
    def display(self)->str:
        """可读全名，如'Ashish Vaswani'/'李炳穆'"""
        if has_cjk(self.family):
            return self.family+self.given
        if self.given:
            return "%s %s"%(self.given,self.family)
        return self.family or self.raw

    def to_dict(self)->Dict[str,str]:
        return{"family":self.family,"given":self.given,"raw":self.raw or self.display}

    @classmethod
    def from_dict(cls,data:Dict[str,Any])->"Author":
        return cls(
            family=_clean_person_text(data.get("family")),
            given=_clean_person_text(data.get("given")),
            raw=_clean_person_text(data.get("raw")),
        )

def _join_name(family:str,given:str)->str:
    return("%s %s"%(given,family)).strip() if given else family

def parse_authors(values:Optional[Iterable[Any]])->List[Author]:
    """批量解析作者列表，自动跳过空项。"""
    if not values:
        return[]
    result:List[Author]=[]
    for item in values:
        author=Author.parse(item)
        if author.family or author.given or author.raw:
            result.append(author)
    return result

@dataclass
class Document:
    """一篇文献的权威表示。"""
    id:str
    title:str
    authors:List[Author]=field(default_factory=list)
    doi:Optional[str]=None
    title_en:Optional[str]=None
    year:Optional[int]=None
    venue:Optional[str]=None
    venue_type:str="unknown"
    volume:Optional[str]=None
    issue:Optional[str]=None
    pages:Optional[str]=None
    publisher:Optional[str]=None
    publisher_place:Optional[str]=None
    edition:Optional[str]=None
    isbn:Optional[str]=None
    url:Optional[str]=None
    access_date:Optional[str]=None
    language:str="en"
    abstract:Optional[str]=None
    fulltext:Optional[str]=None
    keywords:List[str]=field(default_factory=list)
    discipline_tags:List[str]=field(default_factory=list)
    source:str="unknown"
    source_id:Optional[str]=None
    license:Optional[str]=None
    is_oa:bool=False
    pdf_path:Optional[str]=None
    page_count:Optional[int]=None
    citation_count:Optional[int]=None
    references:List[str]=field(default_factory=list)
    content_hash:Optional[str]=None
    fetched_at:Optional[str]=None
    cleaned_at:Optional[str]=None
    summarized_at:Optional[str]=None
    extra:Dict[str,Any]=field(default_factory=dict)

    def __post_init__(self)->None:
        """为每篇文献生成学科标签；显式传入的标签优先保留。"""
        if not isinstance(self.discipline_tags,list):
            self.discipline_tags=[]
        if not self.discipline_tags and (self.title or self.abstract or self.keywords):
            from litagent.disciplines import classify_disciplines

            self.discipline_tags=classify_disciplines(
                title=self.title or "",
                abstract=self.abstract or "",
                keywords=self.keywords,
                venue=self.venue or "",
                fulltext=self.fulltext or "",
            )

    @classmethod
    def make_id(cls,source:str,source_id:Optional[str],doi:Optional[str],
                title:Optional[str])->str:
        """生成稳定ID：优先 DOI，其次数据源ID，最后标题哈希"""
        norm_doi=normalize_doi(doi)
        if norm_doi:
            return "doi:"+norm_doi
        if source_id:
            return "%s:%s"%(source,source_id)
        return "title:"+content_hash(normalize_title(title))

    def to_dict(self)->Dict[str,Any]:
        """序列化"""
        data=asdict(self)
        data["authors"]=[a.to_dict() for a in self.authors]
        return data

    @classmethod
    def from_dict(cls,data:Dict[str,Any])->"Document":
        """反序列化统一走这里，'from_json'与缓存回读都靠它，所有脏数据的兜底都收在这一处"""
        known={f for f in cls.__dataclass_fields__}
        payload={k:v for k,v in data.items() if k in known}  #推导式按字段名过筛，多出来的键一律丢掉
        payload["authors"]=parse_authors(data.get("authors"))
        if not payload.get("id"):
            payload["id"]=cls.make_id(
                payload.get("source","unknown"),
                payload.get("source_id"),
                payload.get("doi"),
                payload.get("title"),
            )
        if payload.get("venue_type") not in VENUE_TYPES:  #venue_type不在枚举里就归到unknown
            payload["venue_type"]="unknown"
        for key in("keywords","references","discipline_tags"):
            if not isinstance(payload.get(key),list):
                payload[key]=[]  #不合法就换成空列表
        if not isinstance(payload.get("extra"),dict):
            payload["extra"]={}
        return cls(**payload)

    def to_json(self)->str:
        return json.dumps(self.to_dict(),ensure_ascii=False)  #ensure_ascii=False是为了保住中文

    @classmethod
    def from_json(cls,raw:str)->"Document":
        return cls.from_dict(json.loads(raw))  #from_json只负责解码一层

    def to_citation_dict(self)->Dict[str,Any]:
        """转换成引用格式化模块约定的输入结构"""
        url=self.url  #先取原始url再按需置空，不做任何归一化
        norm_doi=normalize_doi(self.doi)
        if url and norm_doi:
            stripped=re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/","",
                              url.strip(),flags=re.I).strip().lower()
            if stripped.rstrip(".")==norm_doi:
                url=None

        payload:Dict[str,Any]={  #固定契约字段一次列全
            "id":self.id,
            "doi":self.doi,
            "title":self.title,
            "title_en":self.title_en,
            "authors":[a.to_dict() for a in self.authors],  #作者同样摊平成字典列表
            "year":self.year,
            "venue":self.venue,
            "venue_type":self.venue_type,
            "volume":self.volume,
            "issue":self.issue,
            "pages":self.pages,
            "publisher":self.publisher,
            "publisher_place":self.publisher_place,
            "edition":self.edition,
            "isbn":self.isbn,
            "url":url,  #这里用的是上面判过重的url，不是self.url
            "access_date":self.access_date,
            "language":self.language,
            "discipline_tags":list(self.discipline_tags),
            "extra":self.extra,
        }
        #古籍走的是另一套注释体例，字段名与论文那套完全不同
        if(self.extra or{}).get("kind")=="guji":
            for key in("book_title","book_title_alt","book_part",
                        "chapter_title",
                        "chapter_index","chapter_index_raw","chapter_index_num",
                        "chapter_order_raw","author","author_dynasty",
                        "publisher","publisher_place","edition",
                        "publish_year","page","volume_count","url",
                        "access_date","title_prefix","chronology","collator"):  #末位这几个键和论文那套同名，提升时同样按覆盖处理
                value=(self.extra or{}).get(key)
                if value not in(None,"",[]):
                    payload[key]=value
            payload["is_guji"]=True  #打上is_guji标记
        return payload
    def merge(self,other:"Document")->"Document":
        """把对方文档的非空字段补进当前文档，当前文档已有值优先"""
        for fname in self.__dataclass_fields__:  #字段名遍历复用数据类自身的字段表
            if fname in(
                "id","authors","extra","keywords","references","discipline_tags"
            ):
                continue
            current=getattr(self,fname)
            incoming=getattr(other,fname)  #extra不能整体替换，只能是键级合并
            if(current is None or current=="" or current==[]) and incoming not in(
                None,"",[],
            ):
                setattr(self,fname,incoming)  #setattr按字段名回写

        if len(other.authors)>len(self.authors):
            self.authors=other.authors
        for keyword in other.keywords:  #关键词做并集而不是替换，两个源的关键词互补
            if keyword not in self.keywords:
                self.keywords.append(keyword)
        for ref in other.references:
            if ref not in self.references:
                self.references.append(ref)
        merged_extra=dict(other.extra)
        merged_extra.update(self.extra)  #update的顺序决定优先级
        self.extra=merged_extra
        from litagent.disciplines import classify_disciplines

        self.discipline_tags=classify_disciplines(
            title=self.title or "",
            abstract=self.abstract or "",
            keywords=self.keywords,
            venue=self.venue or "",
            fulltext=self.fulltext or "",
        )
        return self

    def dedupe_key(self)->str:  #去重主键与id是两套东西：id求稳定唯一
        """去重主键：优先归一化DOI，否则退回归一化标题"""
        norm_doi=normalize_doi(self.doi)
        if norm_doi:
            return "doi:"+norm_doi  #前缀区分键的类型
        return "title:"+normalize_title(self.title)

    def compute_content_hash(self)->str:
        """计算全文内容指纹并写入'self.content_hash'"""
        self.content_hash=content_hash(  #算指纹而不是直接比对全文
            self.id,normalize_title(self.title),self.fulltext or self.abstract or ""
        )
        return self.content_hash

    def short_ref(self)->str:  #检索结果列表每行都要显示一个短标识
        """简短引用串，用于检索结果列表展示，形如'Vaswani et al. (2017)'"""
        if not self.authors:
            who="佚名"
        elif len(self.authors)==1:  #单作者直接显示其姓，不加et al.
            who=self.authors[0].family or self.authors[0].display
        else:
            who="%s et al."%(self.authors[0].family or self.authors[0].display)
        year=self.year or "n.d."  #n.d.是无日期的学术惯例写法
        return "%s (%s)"%(who,year)

    def is_empty(self)->bool:
        """是否是一篇没有实际内容的占位文档"""
        return not(self.title or "").strip()  #只有标题为空才算空

@dataclass  #片段与Document是一对多，共用id前缀
class Chunk:
    """文献正文的一个片段，是检索的最小单位"""
    id:str
    doc_id:str
    text:str=""  #text给默认空串是为了支持上面说的两段式构造
    ordinal:int=0
    section:str="body"
    heading:Optional[str]=None
    page_start:Optional[int]=None
    page_end:Optional[int]=None  #页码用Optional：分块不一定落在页边界上
    char_start:Optional[int]=None
    char_end:Optional[int]=None
    token_count:int=0
    content_hash:Optional[str]=None  #词元数只在做长度截断时才用得上，很多来源算不出来
    @classmethod
    def make_id(cls,doc_id:str,ordinal:int,text:str)->str:
        return "%s#%04d-%s"%(doc_id,ordinal,content_hash(text)[:8])  #序号补零到四位，字符串排序才与数值顺序一致

    def to_dict(self)->Dict[str,Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls,data:Dict[str,Any])->"Chunk":
        known={f for f in cls.__dataclass_fields__}  #同样按字段名过筛
        return cls(**{k:v for k,v in data.items() if k in known})

#结构化总结
@dataclass
class Claim:  #text是必填，其余字段可选
    """一条可供引用的论断，是写论文时最直接的弹药"""

    text:str
    section:Optional[str]=None  #页码可选，跨页的论断归到起始页
    page:Optional[int]=None
    evidence:Optional[str]=None

    def to_dict(self)->Dict[str,Any]:  #asdict在这里够用
        return asdict(self)

    @classmethod
    def from_dict(cls,data:Any)->"Claim":  #宽松解析的典型
        if isinstance(data,str):
            return cls(text=data)
        if isinstance(data,dict):
            return cls(
                text=str(data.get("text") or data.get("claim") or ""),  #三个键依次兜底：text、claim、空串
                section=data.get("section"),
                page=data.get("page"),
                evidence=data.get("evidence"),
            )
        return cls(text=str(data))  #既不是字符串也不是字典时强转成字符串

@dataclass
class Summary:
    """模型对一篇文献的结构化总结"""

    doc_id:str  #关联到Document的id
    tldr:str=""
    problem:str=""
    methods:str=""
    findings:str=""
    contributions:str=""
    limitations:str=""
    keywords:List[str]=field(default_factory=list)  #关键词用default_factory
    claims:List[Claim]=field(default_factory=list)
    model:str=""
    prompt_version:str=""
    content_hash:str=""  #指纹用于判断文献变了之后总结是否过期
    created_at:str=""

    def structured_field_count(self)->int:
        """统计六个结构化文本字段中的非空项，用于防止重生成造成内容退化。"""
        names=("tldr","problem","methods","findings","contributions","limitations")
        return sum(1 for name in names if str(getattr(self,name,"") or "").strip())

    def merge(self,other:"Summary")->"Summary":
        """只补齐当前缺失字段并合并论断，不覆盖已有非空内容。"""
        for name in ("tldr","problem","methods","findings","contributions","limitations"):
            if not str(getattr(self,name,"") or "").strip():
                value=getattr(other,name,"")
                if str(value or "").strip():
                    setattr(self,name,value)
        for keyword in other.keywords:
            if keyword not in self.keywords:
                self.keywords.append(keyword)
        existing={claim.text for claim in self.claims}
        for claim in other.claims:
            if claim.text not in existing:
                self.claims.append(claim)
                existing.add(claim.text)
        return self

    def to_dict(self)->Dict[str,Any]:
        data=asdict(self)
        data["claims"]=[c.to_dict() for c in self.claims]
        return data

    @classmethod
    def from_dict(cls,data:Dict[str,Any])->"Summary":
        payload=dict(data)
        payload["claims"]=[Claim.from_dict(c) for c in data.get("claims") or[]]
        if not isinstance(payload.get("keywords"),list):
            payload["keywords"]=[]
        known={f for f in cls.__dataclass_fields__}
        return cls(**{k:v for k,v in payload.items() if k in known})

    def to_json(self)->str:
        return json.dumps(self.to_dict(),ensure_ascii=False)  #总结落盘统一走这里

    def is_empty(self)->bool:
        return not any(
            [self.tldr,self.problem,self.methods,self.findings,  #any套在列表上而不是逐个or连写
             self.contributions,self.limitations,self.claims]
        )


SHORT_SUMMARY_CONTENT_CHARS=300


def summary_quality(
    summary:Summary,doc:Optional[Document]=None,*,
    content_chars:Optional[int]=None,
)->tuple[str,List[str]]:
    """返回总结完整度代码和缺失字段。

    题录级和摘要级文献的可用内容是天然受限的，不能用全文文献的六字段
    完整度直接判为失败。该函数供网页、Agent 和测试共用。
    """
    names=["tldr","problem","methods","findings","contributions","limitations"]
    missing=[name for name in names
             if not str(getattr(summary,name,"") or "").strip()]
    if not missing:
        return "complete",missing

    content_level=str((doc.extra or {}).get("content_level") or "") if doc else ""
    if content_level=="title":
        return "title_limited",missing
    if content_level=="abstract":
        return "abstract_limited",missing
    if content_chars is not None and content_chars<=SHORT_SUMMARY_CONTENT_CHARS:
        return "short_content_limited",missing
    if len(missing)==len(names) and summary.claims:
        return "claims_only",missing
    return "partial",missing


@dataclass
class SearchHit:  #命中片段的id
    """一条检索结果，是多路召回融合排序后的最终形态"""
    chunk_id:str
    doc_id:str
    score:float  #命中片段的正文，检索时只取这一小段而不是整篇文献
    text:str
    section:str="body"
    heading:Optional[str]=None
    page:Optional[int]=None
    doc:Optional[Document]=None  #整篇文献延迟填充
    sources:List[str]=field(default_factory=list)
    ranks:Dict[str,int]=field(default_factory=dict)

    def snippet(self,width:int=320)->str:  #列表展示用的截断方法，检索结果动辄几十条
        """截断片段用于列表展示"""
        text=normalize_whitespace(self.text)
        if len(text)<=width:
            return text
        return text[:width].rstrip()+"…"

    def to_dict(self)->Dict[str,Any]:
        return{
            "chunk_id":self.chunk_id,
            "doc_id":self.doc_id,
            "score":round(self.score,6),  #分数保留六位小数，浮点数的末位噪声对使用者没有意义
            "text":self.text,
            "snippet":self.snippet(),
            "section":self.section,
            "heading":self.heading,
            "page":self.page,
            "sources":list(self.sources),  #列表和字典都拷一份再给出去
            "ranks":dict(self.ranks),
            "doc":self.doc.to_dict() if self.doc else None,
        }

@dataclass  #溯源链路的最终产物
class TraceResult:
    """回答'这段话说的是哪篇论文或哪部典籍'的溯源结果"""
    query:str
    found:bool=False  #默认未命中，只有确实匹配上才翻成真
    confidence:float=0.0
    level:str="none"
    hit:Optional[SearchHit]=None
    candidates:List[SearchHit]=field(default_factory=list)  #候选列表同样用default_factory
    online_candidates:List[Dict[str,Any]]=field(default_factory=list)
    context_before:str=""
    context_after:str=""
    matched_text:str=""  #实际匹配到的那段文本
    note:str=""
    source_label:str=""  #古籍形如《史记》卷七《项羽本纪》
    citations:Dict[str,str]=field(default_factory=dict)
    decision_basis:str="none"
    supporting_signals:Dict[str,Any]=field(default_factory=dict)

    def to_dict(self)->Dict[str,Any]:
        return{
            "query":self.query,
            "found":self.found,
            "confidence":round(self.confidence,4),  #置信度保留四位，与检索分数的六位不同
            "level":self.level,
            "hit":self.hit.to_dict() if self.hit else None,
            "candidates":[c.to_dict() for c in self.candidates],
            "online_candidates":list(self.online_candidates),  #线上候选是原始字典，浅拷一层即可
            "context_before":self.context_before,
            "context_after":self.context_after,
            "matched_text":self.matched_text,
            "note":self.note,
            "source_label":self.source_label,  #出处串原样带出，未命中时它为空串
            "citations":dict(self.citations),
            "decision_basis":self.decision_basis,
            "supporting_signals":dict(self.supporting_signals),
        }

#对外导出清单
__all__=[
    "VENUE_TYPES",
    "SECTION_ALIASES",
    "REFERENCE_HEADINGS",
    "Author",  #九个数据类全部导出，它们构成跨层传递的数据契约
    "Document",
    "Chunk",
    "Claim",
    "Summary",
    "summary_quality",
    "SearchHit",
    "TraceResult",
    "utcnow",  #四个时间与归一化函数是各层共用的工具
    "has_cjk",
    "normalize_whitespace",
    "normalize_doi",
    "normalize_title",
    "title_similarity",
    "content_hash",
    "parse_authors",
]
