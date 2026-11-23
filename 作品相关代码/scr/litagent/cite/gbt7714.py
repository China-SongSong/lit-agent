# -*- coding: utf-8 -*-
"""按 GB/T 7714-2015 生成参考文献著录。"""
#模块级导入
from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from litagent.schema import split_cjk_name

__all__=[
    "TYPE_CODES",
    "CARRIER_CODES",
    "format_gbt7714",
    "format_bibtex",
    "format_ris",
    "format_all",
]

#类型标识表
TYPE_CODES:Dict[str,str]={  #查表用dict而不是一堆if
    "M":"专著（普通图书）",
    "C":"论文集、会议录",
    "G":"汇编",
    "N":"报纸",  #注意是报纸N不是期刊J，报纸走版次不走卷期
    "J":"期刊",
    "D":"学位论文",
    "R":"报告",
    "S":"标准",  #标准文献本身也是文献类型S
    "P":"专利",
    "DB":"数据库",
    "CP":"计算机程序",
    "EB":"电子公告",  #电子公告，[EB/OL]就是它配联机载体
    "A":"档案",
    "CM":"舆图",
    "DS":"数据集",
    "Z":"其他",  #其他Z是真正的兜底位
}

CARRIER_CODES:Dict[str,str]={
    "MT":"磁带",  #载体只有四种，正文里一般只写MT
    "DK":"磁盘",
    "CD":"光盘",
    "OL":"联机网络",
}  #联机网络最常用

_KIND_TYPE_CODE:Dict[str,str]={  #内部venue_type到标准标识的映射表
    "journal":"J",
    "conference":"C",
    "book":"M",
    "chapter":"M",  #与book共用M是标准如此，不是漏写
    "thesis":"D",
    "report":"R",
    "standard":"S",
    "patent":"P",  #专利P与报纸N在这里只差一个字母，改表时最易看漏
    "newspaper":"N",
    "web":"EB",
    "dataset":"DS",
    "preprint":"EB",  #预印本没有专属代码，按电子资源EB著录
    "misc":"Z",
}

_ONLINE_KINDS=("web","dataset","preprint")  #必须含dataset：数据集默认在网上

_BIBTEX_TYPES:Dict[str,str]={
    "journal":"article",
    "conference":"inproceedings",
    "book":"book",
    "chapter":"incollection",
    "thesis":"phdthesis",  #2015版学位论文不分硕博
    "report":"techreport",
    "standard":"misc",
    "patent":"misc",
    "newspaper":"article",
    "web":"misc",
    "dataset":"misc",
    "preprint":"misc",
    "misc":"misc",
}

#RIS映射
_RIS_TYPES:Dict[str,str]={
    "journal":"JOUR",
    "conference":"CPAPER",
    "book":"BOOK",
    "chapter":"CHAP",
    "thesis":"THES",
    "report":"RPRT",
    "standard":"STAND",
    "patent":"PAT",
    "newspaper":"NEWS",
    "web":"ELEC",
    "dataset":"DATA",
    "preprint":"UNPB",
    "misc":"GEN",
}

#正则与转义表
_CJK_RE=re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
                     r"\u3040-\u30ff\uac00-\ud7af]")
_DASH_RE=re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]")
_LATEX_SPECIAL_RE=re.compile(r"[\\&%$#_{}~^]")  #LaTeX里这九个字符都有语法含义
_LATEX_SPECIALS={
    "\\":r"\textbackslash{}",
    "&":r"\&",
    "%":r"\%",
    "$":r"\$",
    "#":r"\#",
    "_":r"\_",
    "{":r"\{",
    "}":r"\}",
    "~":r"\textasciitilde{}",
    "^":r"\textasciicircum{}",
}
_BIBTEX_STOPWORDS=frozenset(  #用frozenset而非set：常量查表不需要可变
    "a an the on in of for and or to with from into at by is are be as".split()
)

#通用小工具
#字段转字符串
def _s(value:Any)->str:
    """把任意字段值安全地转成"去首尾空白"的字符串。"""
    if value is None or isinstance(value,bool):
        return ""  #假值统一给空串
    if isinstance(value,str):
        text=html.unescape(value)
    elif isinstance(value,(int,float)):
        text=str(value)
    else:
        try:  #兜底分支：对象自定义的__str__可能抛异常
            text=str(value)
        except Exception:
            return ""
    text=re.sub(r"<[^>]+>"," ",text)
    return re.sub(r"\s+"," ",text).strip()

#判断是否中文
def _has_cjk(text:str)->bool:
    """文本中是否含中日韩文字，用于判断中文姓名与中文文献"""
    return bool(text) and bool(_CJK_RE.search(text))

#统一成列表
def _as_list(value:Any)->List[Any]:
    """把可能是标量或 'None' 的字段统一成列表"""
    if value is None:
        return[]  #集合也接：去重后的作者集合若不在这里摊平
    if isinstance(value,(list,tuple,set)):
        return[item for item in value]
    return[value]

#取扩展字段
def _extra(doc:Dict[str,Any])->Dict[str,Any]:
    """取出 'doc' 里的 'extra' 子字典，取不到或类型不对时返回空字典"""
    if not isinstance(doc,dict):
        return{}
    value=doc.get("extra")  #取值后立刻验证类型，extra是外部塞进来的
    return value if isinstance(value,dict) else{}

#宽松真值判断
def _truthy(value:Any)->bool:
    """宽松真值判断，'True'、'true'、'1'、'yes'、'y'、'on' 都算真"""
    if isinstance(value,bool):
        return value  #统一转小写再比，这样True和true都能命中
    text=_s(value).lower()
    return text in("1","true","yes","y","on")

#页码归一化
def _clean_pages(value:Any)->str:
    """页码归一化，把各种破折号统一成半角连字符"""
    text=_s(value)
    if not text:  #早退返回空串，避免对空值跑两次正则替换
        return ""
    text=_DASH_RE.sub("-",text)
    text=re.sub(r"\s*-\s*","-",text)
    return re.sub(r"\s+"," ",text).strip()

def _clean_title(value:Any)->str:
    """题名归一化，压缩空白并去掉信息源里多余的结尾句点"""
    text=re.sub(r"\s+"," ",_s(value))  #连换行一起压成单空格
    return text.rstrip(".").strip()

#日期归一化
def _norm_date(value:Any)->str:
    """日期归一化，'2025-1-1'、'2025/01/01'、'2025年1月1日' 都归一成 '2025-01-01'"""
    text=_s(value)
    if not text:  #早退：空日期不参与匹配
        return ""
    match=re.match(
        r"^(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?$",text
    )
    if match:  #识别成功就补零输出
        return "%04d-%02d-%02d"%(
            int(match.group(1)),int(match.group(2)),int(match.group(3))
        )
    match=re.match(r"^(\d{4})\s*[-/.年]\s*(\d{1,2})\s*月?$",text)  #只有年月也算合法
    if match:
        return "%04d-%02d"%(int(match.group(1)),int(match.group(2)))
    return text

#页码拆分
def _split_pages(pages:str)->Tuple[str,str]:
    """把 '5998-6008' 拆成起讫两页，单页返回起始页与空串"""
    text=_clean_pages(pages)
    if not text:  #早退给("", "")而不是None
        return "",""
    match=re.match(r"^([A-Za-z]?\d+(?:\.\d+)?)\s*-\s*([A-Za-z]?\d+(?:\.\d+)?)$",text)
    if match:
        return match.group(1),match.group(2)  #能拆就交还原样的两段，不做数值规整
    return text,""

def _ordinal(number:int)->str:
    """把数字转成英文序数词"""
    if 10<=number%100<=20:
        suffix="th"
    else:
        suffix={1:"st",2:"nd",3:"rd"}.get(number%10,"th")
    return "%d%s"%(number,suffix)

def _initials(given:str)->str:  #这个函数只吐首字母，成串的调用方在下面自己拼
    """把名压成首字母串，如 'Ashish' 得 'A'、'David A' 得 'D A'"""
    if not given:
        return ""
    result:List[str]=[]
    for token in re.split(r"[\s\-\.\u00b7]+",given):  #空白、连字符、句点和间隔号一起当分隔符
        for char in token:
            if char.isalnum():
                result.append(char.upper())
                break  #用的是.join而不是字符串相加
    return " ".join(result)

def _split_raw(raw:str)->Tuple[str,str]:
    """'family' 与 'given' 都缺时，从整名 'raw' 里兜底解析出 (姓, 名)"""
    text=_s(raw)
    if not text:
        return "",""
    if _has_cjk(text):
        return split_cjk_name(text)
    if "," in text:
        family,_,given=text.partition(",")  #有逗号就是最明确的信号
        return family.strip(),given.strip()
    tokens=text.split()
    if len(tokens)==1:
        return tokens[0],""
    last=tokens[-1].rstrip(".")
    if len(last)<=3 and re.match(r"^[A-Za-z.]+$",tokens[-1]):
        return " ".join(tokens[:-1]),tokens[-1]
    first=tokens[0].rstrip(".")
    if len(tokens)==2 and len(first)<=2 and re.match(r"^[A-Za-z.]+$",tokens[0]):  #首位短且只有两段时反过来认
        return tokens[1],tokens[0]
    return tokens[0]," ".join(tokens[1:])

def _author_family_given(author:Any)->Tuple[str,str]:
    """从作者字典或字符串里取出 (姓, 名)，必要时用整名 'raw' 兜底"""
    if isinstance(author,str):  #传进来是字符串就当只有raw字段
        author={"raw":author}
    if not isinstance(author,dict):
        return "",""
    family=_s(author.get("family") or author.get("familyName"))  #family和familyName两种键都认
    given=_s(author.get("given") or author.get("givenName"))
    raw=_s(author.get("raw") or author.get("name"))
    if not family and not given:
        if raw:  #raw有值才去解析，没值就保持两个空串
            family,given=_split_raw(raw)
        return family,given
    if family and not given and raw:
        #"李炳穆" + family="李" 这类情况
        rest=raw.replace(family," ",1).strip(" ,.")
        if rest and len(rest)<len(raw):
            given=rest
    return family,given

def _format_author_gbt(author:Any)->str:
    """单个著者的 GB/T 7714-2015 形式。"""
    family,given=_author_family_given(author)
    if not family and not given:  #姓和名都没有就返回空串
        return ""
    if _has_cjk(family) or _has_cjk(given):
        return(family+given).strip()
    family_out=re.sub(r"\s+"," ",family).upper()  #西文姓先压掉内部空白再全大写
    initials=_initials(given)
    return(family_out+" "+initials).strip()

def _format_authors_gbt(authors:Any)->str:
    """著者串：不超过 3 人全部照录，超过 3 人只录前 3 人再加 '等' 或 'et al.'"""
    names:List[str]=[]
    for author in _as_list(authors):
        name=_format_author_gbt(author)
        if name:
            names.append(name)
    if not names:
        return ""
    if len(names)<=3:  #三人和三人以下全部照录
        return ", ".join(names)
    head=", ".join(names[:3])
    marker="等" if _has_cjk(head) else "et al."
    return head+", "+marker  #中文补等、西文补et al.

def _bibtex_author(author:Any)->str:
    """BibTeX 著者项：西文写 'Family, Given'，中文连排不拆"""
    family,given=_author_family_given(author)  #BibTeX惯例是Family, Given
    if not family and not given:
        return ""
    if _has_cjk(family) or _has_cjk(given):
        return(family+given).strip()
    if given:  #只有名的场合才加逗号
        return family+", "+given
    return family

def _ris_name(author:Any)->str:
    """RIS 的 'AU' 取值：西文写 'Family, Given'，中文连排"""
    return _bibtex_author(author)

#类型推断与标识
def _resolve_kind(doc:Dict[str,Any])->str:
    """确定文献的有效类型"""
    venue_type=_s(doc.get("venue_type")).lower()  #统一小写再比，元数据里写成Journal也能命中
    if venue_type in _KIND_TYPE_CODE:
        return venue_type
    if _s(doc.get("volume")) or _s(doc.get("issue")):
        return "journal"
    if(_s(doc.get("publisher")) or _s(doc.get("publisher_place"))  #出版者、出版地、书号、版本任意一个有
            or _s(doc.get("isbn")) or _s(doc.get("edition"))):
        return "book"
    if _s(doc.get("url")):
        return "web"
    return "misc"  #misc是最后的兜底，对外表现为类型标识Z

def _type_identifier(doc:Dict[str,Any],kind:str)->str:
    """返回 'J'、'J/OL'、'EB/OL'、'M/CD' 这类文献类型标识"""
    base=_KIND_TYPE_CODE.get(kind,"Z")  #查表取类型代码，查不到给Z
    carrier=_s(_extra(doc).get("carrier")).upper()
    if not carrier and(kind in _ONLINE_KINDS or _s(doc.get("url"))):
        carrier="OL"
    return base+"/"+carrier if carrier else base  #有载体才拼斜杠，没有就交还原标识

def _is_zh_doc(doc:Dict[str,Any])->bool:
    """判断是否按中文文献处理"""
    language=_s(doc.get("language")).lower()
    if language:
        return language.startswith("zh") or language in("chi","chinese","cn")
    probe=_s(doc.get("title"))
    for author in _as_list(doc.get("authors")):
        family,given=_author_family_given(author)  #姓名也要拼进探针
        probe+=family+given
    return _has_cjk(probe)

#著录项拼装
def _publish_block(doc:Dict[str,Any],is_zh:bool,with_year:bool,  #这个函数只负责出版地加出版者加出版年
                   placeholder:bool)->str:
    """出版项，拼成 '出版地: 出版者, 出版年'"""
    place=_s(doc.get("publisher_place"))
    publisher=_s(doc.get("publisher"))
    year=_s(doc.get("year"))
    if place and publisher:
        segment=place+": "+publisher  #两个都有时隔冒号，:是出版者的前置符
    elif place:
        if placeholder:
            segment=place+": "+("[出版者不详]" if is_zh else "[s.n.]")
        else:
            segment=place  #电子资源不塞占位语
    elif publisher:
        if placeholder:
            segment=("[出版地不详]: " if is_zh else "[S.l.]: ")+publisher
        else:
            segment=publisher  #电子资源只写出版者，不加冒号也不加占位语
    else:
        segment=""
    if with_year and year:
        segment=(segment+", "+year) if segment else year  #有前段才用逗号加年份，没前段就直接是年份
    return segment

def _with_pages(segment:str,pages:str)->str:
    """把页码接到出版项或出处项之后，如 '北京: 中华书局, 2000' 加 ': 29'"""
    if not pages:  #有前段才补冒号，否则页码自成一段
        return segment
    return(segment+": "+pages) if segment else pages

def _period_number(year:str,volume:str,issue:str,mode:str)->str:
    """期刊的年卷期标识"""
    if volume and issue:
        base=volume+"("+issue+")"
    elif volume:
        base=volume  #只有卷没有期就只写卷号，不硬凑一对空括号
    elif issue:
        base="("+issue+")"
    else:
        base=""  #两样都没有给空串
    if mode=="author_year":
        return base
    if year and base:
        #有卷号时 年, 卷(期)
        return(year+", "+base) if volume else(year+base)
    return year or base

def _format_edition(value:Any,is_zh:bool)->str:
    """版本项（8.3）：第 1 版不著录，'3' 写成 '3版' 或 '3rd ed.'"""
    text=_s(value).strip().rstrip(".")
    if not text:
        return ""
    low=text.lower()
    if low in("1","1st","1st ed","first edition","第1版","第一版","初版"):  #1、1st、第1版、初版都算第1版
        return ""
    match=re.match(r"^(\d+)$",low)
    if match:
        number=int(match.group(1))
        if number==1:  #数字是1就返回空串，这一条把1和第1版统一拦掉
            return ""
        return("%d版"%number) if is_zh else(_ordinal(number)+" ed.")
    match=re.match(r"^(\d+)(st|nd|rd|th)(\s+ed(\.|ition)?)?$",low)
    if match:
        if int(match.group(1))==1:  #带后缀的写法同样要把第1版拦掉
            return ""
        return low+"."
    if _has_cjk(text):
        return text
    return text+"." if not text.endswith(".") else text  #西文自拟版本补句点，已经有点就不再补

def _format_translators(value:Any,is_zh:bool)->str:
    """其他责任者（译者），如 '谢远涛, 译'"""
    names:List[str]=[]
    for item in _as_list(value):  #译者来源可能是名字字符串也可是作者字典
        if isinstance(item,dict):
            name=_format_author_gbt(item) or _s(item.get("name"))
        else:
            name=_s(item)  #字符串直接取，与上面字典分支汇合
        if name:
            names.append(name)
    if not names:
        return ""
    return ", ".join(names)+(", 译" if is_zh else ", trans.")  #中文补, 译、西文补, trans.

def _join_parts(parts:Sequence[str])->str:
    """用前置符 '. ' 连接各著录项，并在结尾补一个句点"""
    cleaned:List[str]=[]
    for part in parts:
        text=_s(part)
        if text:  #每段先去掉尾部句点再连接
            cleaned.append(text.rstrip(".").strip())
    cleaned=[item for item in cleaned if item]
    if not cleaned:
        return ""
    return ". ".join(cleaned)+"."  #整条都空就给空串

def _attach(parts:List[str],text:str)->None:
    """把文本紧贴到上一个著录项之后，用于 '[引用日期]'"""
    text=_s(text)
    if not text:  #空文本直接返回，不产生任何修改
        return
    if parts:
        parts[-1]=parts[-1]+text
    else:
        parts.append(text)  #一个著录项都还没拼出来时

def _build_gbt(doc:Any,page:Optional[str],mode:str)->str:
    """内部核心：按 'mode'（'numeric' 或 'author_year'）拼出一条著录"""
    if not isinstance(doc,dict):
        return ""  #不是字典就早退，上游可能把None或列表传进来
    kind=_resolve_kind(doc)
    is_zh=_is_zh_doc(doc)
    extra=_extra(doc)

    year=_s(doc.get("year"))
    venue=_s(doc.get("venue"))
    url=_s(doc.get("url"))
    doi=_s(doc.get("doi"))
    access=_norm_date(doc.get("access_date"))
    #page是引文页码
    pages=_clean_pages(page) or _clean_pages(doc.get("pages"))
    authors=_format_authors_gbt(doc.get("authors"))

    code=_type_identifier(doc,kind)
    online="/" in code
    update=_norm_date(extra.get("update_date") or extra.get("modified_date"))
    suppress_year=bool(  #三个条件同时成立才压年份
        online and update
        and not _s(doc.get("publisher")) and not _s(doc.get("publisher_place"))
    )
    with_year=(mode=="numeric") and not suppress_year  #顺序编码制且没被压掉时才带出版年

    parts:List[str]=[]

    if mode=="author_year":
        parts.append(authors or("佚名" if is_zh else "Anon."))
        if year:
            parts.append(year)
    elif authors:
        parts.append(authors)

    title=_clean_title(doc.get("title"))
    other_title=""
    if kind=="standard":
        other_title=(_s(extra.get("standard_number")) or _s(extra.get("number"))
                       or _s(doc.get("isbn")))
    elif kind=="report":
        other_title=_s(extra.get("report_number")) or _s(extra.get("number"))
    elif kind=="patent":
        number=(_s(extra.get("patent_number")) or _s(extra.get("number"))
                  or _s(doc.get("venue")))
        country=_s(extra.get("patent_country"))
        other_title=(country+", "+number) if(country and number) else number

    title_part=""
    if title or other_title:
        title_part=(title+": "+other_title) if(title and other_title) \
            else(title or other_title)
        #按类型分支
        title_part+="["+code+"]"

    if kind=="journal":  #期刊分支：题名之后接刊名加年卷期加页码
        parts.append(title_part)
        period=_period_number(year,_s(doc.get("volume")),_s(doc.get("issue")),mode)
        source=", ".join([item for item in(venue,period) if item])
        if pages:  #有页码才补冒号，页码是冒号前置符
            source=(source+": "+pages) if source else pages
        if source:
            parts.append(source)

    elif kind=="newspaper":
        #8.8.5：报纸应在报纸名后著录出版日期与版次
        parts.append(title_part)
        date=_norm_date(extra.get("date") or extra.get("publication_date")) or year
        edition=_s(extra.get("edition_number")) or pages  #报纸没有卷期，页码位放的就是版次
        source=venue
        if date:
            source=(source+", "+date) if source else date
        if edition:
            source=(source+"("+edition+")") if source else "("+edition+")"  #版次套在小括号里紧贴日期
        if source:
            parts.append(source)

    elif kind in("conference","chapter"):
        container=title_part  #析出文献题名要整体作为容器的头部
        container_author=_s(extra.get("container_author")
                              or extra.get("proceedings_editor"))
        if venue:
            container+="//"+((container_author+". "+venue)  #//前不留空格
                                 if container_author else venue)
        elif container_author:
            container+=". "+container_author
        parts.append(container)
        publish=_publish_block(doc,is_zh,with_year=with_year,  #有//的析出文献用占位符，缺出版项时占住位置
                                 placeholder=True)
        segment=_with_pages(publish,pages)
        if segment:
            parts.append(segment)

    elif kind in("book","misc"):
        parts.append(title_part)
        translators=_format_translators(extra.get("translators"),is_zh)  #译者自成一项跟在题名后，缺译者时整项不出现
        if translators:
            parts.append(translators)
        edition=_format_edition(doc.get("edition"),is_zh)
        if edition:
            parts.append(edition)
        publish=_publish_block(doc,is_zh,with_year=with_year,
                                 placeholder=True)
        segment=_with_pages(publish,pages)
        if segment:
            parts.append(segment)

    elif kind in("thesis","report","standard"):
        #学位论文、报告、标准共用一套著录骨架
        parts.append(title_part)
        publish=_publish_block(doc,is_zh,with_year=with_year,
                                 placeholder=True)
        segment=_with_pages(publish,pages)
        if segment:
            parts.append(segment)

    elif kind=="patent":
        parts.append(title_part)
        date=_norm_date(extra.get("announcement_date") or extra.get("date")  #公告日期是专利的著录正日，取不到才退回年份
                          or extra.get("publication_date")) or year
        if date:
            parts.append(date)

    else:  #web / dataset / preprint
        parts.append(title_part)
        publish=_publish_block(doc,is_zh,with_year=with_year,
                                 placeholder=False)
        if pages:
            publish=(publish+": "+pages) if publish else pages
        if publish:
            parts.append(publish)

    #更新(修改)日期与引用日期只属于联机文献
    if online and update:
        parts.append("("+update+")")
    if online and access:
        _attach(parts,"["+access+"]")
    if url:
        parts.append(url)  #获取路径单独成项，通常是整条里最长的一项
    if doi and(online or _truthy(extra.get("force_doi"))
                or _truthy(extra.get("doi_required"))):
        parts.append("DOI: "+doi)  #DOI: 前缀是必需的

    meaningful=[item for item in parts if _s(item)]
    if mode=="author_year" and meaningful==["佚名" if is_zh else "Anon."]:
        return ""
    return _join_parts(parts)

#对外 API
def format_gbt7714(doc:dict,*,page:Optional[str]=None)->str:
    """返回一条 GB/T 7714-2015 顺序编码制著录（不含序号）。"""
    return _build_gbt(doc,page,"numeric")

#批量加序号

#著者-出版年制

#正文标注与连续序号缩写

#BibTeX
def _latex_escape(text:str)->str:
    """转义 LaTeX 特殊字符：'& % $ # _ { } ~ ^ '（'~'/'^'/'\' 用命令形式）。"""
    return _LATEX_SPECIAL_RE.sub(lambda m:_LATEX_SPECIALS[m.group(0)],text)  #用正则一次扫完所有特殊字符，回调查表替换

def _bibtex_value(text:str)->str:
    """BibTeX 字段值：先转义 LaTeX 特殊字符，含中文时整串用 '{}' 保护大小写。"""
    escaped=_latex_escape(_s(text))
    if _has_cjk(escaped):
        return "{"+escaped+"}"
    return escaped

#自动生成 key
def _bibtex_key(doc:Dict[str,Any])->str:
    """自动生成 BibTeX key：'姓 + 年 + 题名首实词'，如 'vaswani2017attention'。"""
    authors=_as_list(doc.get("authors"))
    family=""
    if authors:
        family,_given=_author_family_given(authors[0])
    token=re.sub(r"[^A-Za-z]","",(family.split() or[""])[0])
    if not token:
        token=re.sub(r"[^A-Za-z0-9]","",_s(doc.get("id")))
    if not token:
        token="ref"
    year=_s(doc.get("year")) or "nd"
    word=""
    for candidate in re.split(r"[^0-9A-Za-z]+",_s(doc.get("title"))):
        lowered=candidate.lower()
        if len(lowered)>=3 and lowered not in _BIBTEX_STOPWORDS \
                and re.search(r"[a-z]",lowered):
            word=lowered
            break
    return(token.lower()+year+word) or "ref"

#条目类型推断
def _bibtex_entry_type(doc:Dict[str,Any],kind:str)->str:
    """推断 BibTeX 条目类型；学位论文按 'extra["thesis_type"]'/题名区分硕博。"""
    entry_type=_BIBTEX_TYPES.get(kind,"misc")
    if entry_type=="phdthesis":
        hint=(_s(_extra(doc).get("thesis_type"))+" "+_s(doc.get("title"))
                +" "+_s(_extra(doc).get("degree"))).lower()
        if any(word in hint for word in("硕士","master","msc","m.sc")):  #硕博无法从类型直接分辨
            return "mastersthesis"
    return entry_type

#单条 BibTeX 渲染
def format_bibtex(doc:dict,*,key:Optional[str]=None)->str:
    """返回一条 BibTeX 记录，字段按文献类型择用；整条无内容时返回空串。"""
    if not isinstance(doc,dict):
        doc={}
    kind=_resolve_kind(doc)
    entry_type=_bibtex_entry_type(doc,kind)
    extra=_extra(doc)
    fields:List[Tuple[str,str]]=[]

    def add(name:str,value:Any)->None:
        text=_s(value)
        if text:  #空值直接不落库，省得渲染阶段再逐条过滤
            fields.append((name,text))

    author_names=[name for name in(_bibtex_author(a)
                                      for a in _as_list(doc.get("authors"))) if name]
    if author_names:
        add("author"," and ".join(author_names))  #BibTeX 多著者用 and 连接，这是格式规定
    add("title",_clean_title(doc.get("title")))
    if kind in("journal","newspaper"):
        add("journal",doc.get("venue"))
    elif kind in("conference","chapter"):
        add("booktitle",doc.get("venue"))  #会议和章节的出处进booktitle
    add("year",doc.get("year"))
    add("volume",doc.get("volume"))
    add("number",doc.get("issue") or extra.get("report_number")
        or extra.get("standard_number") or extra.get("patent_number"))
    add("pages",_clean_pages(doc.get("pages")))
    if kind=="report":
        add("institution",doc.get("publisher"))  #报告的出版者语义上是机构
    elif kind=="thesis":
        add("school",doc.get("publisher"))
    else:
        add("publisher",doc.get("publisher"))
    add("address",doc.get("publisher_place"))
    if kind=="book" or doc.get("edition"):
        edition_raw=_s(doc.get("edition"))
        if re.match(r"^\d+$",edition_raw) and not _is_zh_doc(doc):  #纯数字版本转序数形式，中文文献不转
            edition_raw=_ordinal(int(edition_raw))
        add("edition",edition_raw)
    if entry_type=="misc":
        type_label={
            "standard":"Standard",
            "patent":"Patent",
            "dataset":"Dataset",
            "preprint":"Preprint",
            "web":"Web",
        }.get(kind)
        if type_label:
            add("type",type_label)
    add("doi",doc.get("doi"))
    add("isbn",doc.get("isbn"))
    add("url",doc.get("url"))
    add("urldate",_norm_date(doc.get("access_date")))
    if kind=="preprint":
        add("eprint",extra.get("arxiv_id") or extra.get("eprint"))
        if extra.get("arxiv_id") or extra.get("eprint"):  #有预印本编号才补archivePrefix
            add("archivePrefix","arXiv")
    if kind=="newspaper":
        date=_norm_date(extra.get("date") or extra.get("publication_date"))
        edition=_s(extra.get("edition_number")) or _clean_pages(doc.get("pages"))
        note=date+(("("+edition+")") if edition else "")
        add("note",note)
    elif kind=="patent":
        add("note",_norm_date(extra.get("announcement_date") or extra.get("date")))

    if not fields:
        #整个字段表都是空的说明这条文献没内容，早退返空串
        return ""
    rendered=["@%s{%s,"%(entry_type,_s(key) or _bibtex_key(doc))]
    for name,value in fields:
        rendered.append("  %s = {%s},"%(name,_bibtex_value(value)))
    rendered.append("}")
    return "\n".join(rendered)

#单条 RIS 渲染
def format_ris(doc:dict)->str:
    """返回一条 RIS 记录，末行为 'ER  - '（标签 + 两空格 + 连字符 + 空格）。"""
    if not isinstance(doc,dict):
        doc={}
    kind=_resolve_kind(doc)
    extra=_extra(doc)
    lines:List[str]=["TY  - "+_RIS_TYPES.get(kind,"GEN")]  #TY必须排第一行且不能被过滤掉

    def add(tag:str,value:Any)->None:
        text=_s(value)
        if text:  #空字段整行不写
            lines.append("%s  - %s"%(tag,text))

    for author in _as_list(doc.get("authors")):
        add("AU",_ris_name(author))  #每位著者单独一行AU
    add("T1",_clean_title(doc.get("title")))
    if kind=="journal":
        add("JO",doc.get("venue"))
    if kind in("journal","newspaper","conference","chapter"):  #这四种都有容器刊名，所以再补一条T2
        add("T2",doc.get("venue"))
    add("Y1",doc.get("year"))
    add("DA",_norm_date(extra.get("date") or extra.get("publication_date")
                         or extra.get("announcement_date")))
    add("VL",doc.get("volume"))
    add("IS",doc.get("issue"))
    start,end=_split_pages(_clean_pages(doc.get("pages")))
    if kind=="newspaper":
        start,end="",""  #报纸的页码是版次，RIS 里没有对应标签
        add("M1",extra.get("edition_number") or _clean_pages(doc.get("pages")))
    add("SP",start)
    add("EP",end)
    add("PB",doc.get("publisher"))
    add("CY",doc.get("publisher_place"))
    add("ET",doc.get("edition"))  #ET放版次
    add("SN",doc.get("isbn") or extra.get("standard_number")
        or extra.get("report_number"))
    add("DO",doc.get("doi"))
    add("UR",doc.get("url"))
    add("Y2",_norm_date(doc.get("access_date")))
    if len(lines)<=1:
        #只有T一行说明一个字段都没填上，早退返空串
        return ""
    lines.append("ER  - ")
    return "\n".join(lines)

#一次导出三种格式
def format_all(doc:dict,*,page:Optional[str]=None)->Dict[str,str]:
    """一次返回 '{"gbt7714": ..., "bibtex": ..., "ris": ...}'。"""
    return{
        "gbt7714":format_gbt7714(doc,page=page),
        "bibtex":format_bibtex(doc),
        "ris":format_ris(doc),
    }
