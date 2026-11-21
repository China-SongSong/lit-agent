# -*- coding: utf-8 -*-
"""中文历史典籍（古籍）引用格式化：历史学注释体例 + GB/T 7714-2015。"""
from __future__ import annotations

import re
import html
from typing import Any,Dict,List,Optional

__all__=[
    "HISTORY_NOTE_RULE",  #HISTORY_NOTE_RULE排在首位
    "format_history_note",
    "format_gbt7714_guji",  #正文夹注单独一个入口
    "format_guji_all",
    "describe_source",
]

#体例依据
HISTORY_NOTE_RULE=(
    "《历史研究》关于引文注释的规定（古籍部分）："
    "责任者与责任方式／文献题名／卷次、篇名、部类（选项）／版本或出版信息／页码；"  #字段顺序照抄规范原文，谁在前谁在后不是排版偏好
    "篇名与部类名用书名号，层次间用中圆点；卷次用阿拉伯数字；"
    "再次引证只标责任者、题名、页码；注释序号用①②③，每页单独排序。"
)
  #注释序号用带圈的①②③、且每页单独排序

#书名号包裹符
_WRAPPER_RE=re.compile(r"[《》〈〉「」『』【】\[\]]")

#著者分隔符
_AUTHOR_SPLIT_RE=re.compile(r"\s*[,，、;；]\s*")

#页码形态
_PAGE_TOKEN=r"\d+[a-zA-Z上下栏面]{0,3}"
_PAGE_RANGE_RE=re.compile(r"^(%s)\s*[-–—~～]\s*(%s)$"%(_PAGE_TOKEN,_PAGE_TOKEN))
_PAGE_SUFFIX_RE=re.compile(r"^(\d+)\s*([a-zA-Z上下栏面]{1,3})$")

_DATE_RE=re.compile(r"^(\d{4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})$")  #引用日期先归一成同一种写法再比大小

_CN_DATE_RE=re.compile(r"^(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日$")

#版本判定
_FIRST_EDITION_RE=re.compile(r"^(?:第?\s*1\s*版|初版|首版|1st\s*ed\.?)$",re.IGNORECASE)

_CIRCLED_1="①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"  #带圈序号只列到 20，一个页面 20 条注足够用
_CIRCLED_2="㉑㉒㉓㉔㉕㉖㉗㉘㉙㉚㉛㉜㉝㉞㉟"
_CIRCLED_3="㊱㊲㊳㊴㊵㊶㊷㊸㊹㊺㊻㊼㊽㊾㊿"

def _s(value:Any)->str:
    """把任意值安全地取成去掉首尾空白的字符串；None/False/空值 -> ""。"""
    if value is None or isinstance(value,bool):  #bool要单独拦
        return ""
    if isinstance(value,str):
        text=html.unescape(value)
    elif isinstance(value,int):
        text=str(value)
    else:
        text=str(value)
    text=re.sub(r"<[^>]+>"," ",text)
    return re.sub(r"\s+"," ",text).strip()

def _extra(doc:Dict[str,Any])->Dict[str,Any]:
    """取出 ``extra`` 字典（不是 dict 时返回空字典）。"""  #取不到就返回空字典而不是None
    value=doc.get("extra")
    return value if isinstance(value,dict) else{}

def _ensure_doc(doc:Any)->Dict[str,Any]:
    """入参必须是 ``dict``；字段缺失用 ``None``，但类型错误要立刻暴露。"""  #入参只接受字典：缺字段可以用None顶
    if not isinstance(doc,dict):
        raise TypeError("doc 必须是 dict（缺失字段请用 None），收到 %s"%type(doc).__name__)
    return doc

def _clean_name(value:Any)->str:
    """去掉包裹用的书名号、引号与首尾空白（契约要求输入不含书名号，此处做兜底）。"""
    return _WRAPPER_RE.sub("",_s(value)).strip()  #剥完再取一次空白

def _join_authors(text:str)->str:
    """多著者统一用、连接（《历史研究》示例：谭汝谦、林启彦译）。"""
    parts=[p for p in _AUTHOR_SPLIT_RE.split(text) if p]  #推导式顺带过滤空片段
    return "、".join(parts) if parts else ""

#数字转换表

#汉字数字
_CN_DIGITS:Dict[str,int]={
    "零":0,"〇":0,"○":0,
    "一":1,"壹":1,
    "二":2,"贰":2,"貳":2,"两":2,"兩":2,  #繁体貳和兩不能漏：港台影印本的卷次就用繁体
    "三":3,"叁":3,"參":3,
    "四":4,"肆":4,
    "五":5,"伍":5,
    "六":6,"陆":6,"陸":6,  #陆和陸分别是简体与繁体，两个字都收
    "七":7,"柒":7,
    "八":8,"捌":8,
    "九":9,"玖":9,
}
_CN_UNITS:Dict[str,int]={  #单位表只到千：卷次上五位数极罕见
    "十":10,"拾":10,
    "百":100,"佰":100,
    "千":1000,"仟":1000,
}
#拼写序列
_CN_DIGIT_ORDER="零一二三四五六七八九"

#卷次转整数
def _cn_numeral_to_int(text:str)->Optional[int]:
    """把七十二二百八十二7这类卷次写法转成整数，认不出时返回 None"""
    token=_s(text)
    if not token:  #空串早退，后面每次循环取字符都要先有内容
        return None
    if token.isdigit():
        try:
            return int(token)
        except ValueError:
            return None
    total=0  #百位以上大单位的累加器先置零，循环里逐段攒
    section=0
    number=0
    seen=False
    for ch in token:
        if ch in _CN_DIGITS:
            number=_CN_DIGITS[ch]
            seen=True
        elif ch in _CN_UNITS:  #取出单位对应的数值，注意这里是查表取值
            unit=_CN_UNITS[ch]
            if number==0 and unit==10:
                number=1  #没有前导数字就按一处理
            section+=number*unit
            number=0
            seen=True
        else:  #遇到表里没有的字符直接返回None
            return None
    if not seen:
        return None
    return total+section+number  #三段相加：段值是百千这类大单位攒的

def _int_to_cn_numeral(number:int)->str:
    """把 1—9999 的整数转成汉字数字（7→七，282→二百八十二）。"""
    if number<=0:  #非正数直接空串：卷次没有零卷这种写法
        return ""
    if number>9999:
        return str(number)
    units=("","十","百","千")  #四个单位从低到高排，索引正好等于数的位次
    digits=[]
    rest=number
    for _ in range(4):
        digits.append(rest%10)  #取最低位，交给列表头
        rest//=10
    out=""
    zero_pending=False
    for pos in range(3,-1,-1):
        digit=digits[pos]
        if digit==0:
            if out:
                zero_pending=True
            continue
        if zero_pending:
            out+="零"
            zero_pending=False  #补完就撤标记，避免连着的多个零被重复写出
        if not(pos==1 and digit==1 and not out):
            out+=_CN_DIGIT_ORDER[digit]
        out+=units[pos]  #再挂上单位，个位的单位是空串，等价于不写
    return out

#卷次数值抽取
def _index_number(doc:Dict[str,Any])->Optional[int]:
    """从 chapter_index（优先）或 chapter_index_raw 抽出卷次的阿拉伯数值。"""
    for key in("chapter_index","chapter_index_raw"):
        token=_s(doc.get(key))  #用_s取值
        if not token:
            continue
        cleaned=token.replace("卷","").replace("册","").replace("冊","").strip()
        if cleaned.startswith("第"):  #第七卷这种带第的写法要再剥一层
            cleaned=cleaned[1:].strip()
        value=_cn_numeral_to_int(cleaned)
        if value is not None:
            return value
    return None

#部志名：三国的魏蜀吴各自从卷一编号
def _part(doc:Dict[str,Any])->str:
    """部/志名，如《三国志》的魏志。"""
    return _s(doc.get("book_part"))

#部名并入卷次
def _with_part(doc:Dict[str,Any],index_text:str)->str:
    """把部名并进卷次串：魏志+卷1→魏志卷1。"""
    part=_part(doc)
    if not part or not index_text or part in index_text:  #三个条件任一成立就不拼
        return index_text
    return part+index_text

#引用用卷次串
def _index_citation_text(doc:Dict[str,Any],style:str)->str:
    """生成引用用的卷次串，如卷7/卷七/原文写法。"""
    raw=_s(doc.get("chapter_index_raw"))
    number=_index_number(doc)  #再算一遍阿拉伯数值，解析不出来时返回 None
    if style=="raw":
        if raw:
            return _with_part(doc,raw if "卷" in raw else "卷"+raw)
        if number is not None:  #原文写法缺失但有可解析的数值，退一步用数值补出卷次
            return _with_part(doc,"卷%s"%number)
        return ""
    if number is None:
        #数字解析不出来（如卷上卷首）时退回原文
        if raw:
            return _with_part(doc,raw if "卷" in raw else "卷"+raw)
        token=_s(doc.get("chapter_index"))
        if not token:  #三个来源全空，返回空串
            return ""
        return _with_part(doc,token if "卷" in token else "卷"+token)
    if style=="chinese":
        return _with_part(doc,"卷"+_int_to_cn_numeral(number))  #换算只处理数值
    return _with_part(doc,"卷%d"%number)

#展示用卷次串
def _index_display_text(doc:Dict[str,Any])->str:
    """生成展示用的卷次串：尽量保留原文写法（供 :func:`describe_source` 使用）。"""
    raw=_s(doc.get("chapter_index_raw"))
    if raw:  #有原文就整段采用，不再往下走数字解析
        return _with_part(doc,raw if "卷" in raw else "卷"+raw)
    token=_s(doc.get("chapter_index"))
    if not token:
        return ""
    cleaned=token.replace("卷","").replace("册","").replace("冊","").strip()  #清洗与_index_number同一套
    if cleaned.startswith("第"):
        cleaned=cleaned[1:].strip()
    number=_cn_numeral_to_int(cleaned)
    if number is None:  #解析不出来就原样保留输入token
        return _with_part(doc,("卷"+token) if "卷" not in token else token)
    if cleaned.isdigit():
        return _with_part(doc,"卷%s"%number)
    return _with_part(doc,"卷"+_int_to_cn_numeral(number))  #输入是汉字数字（七）就写卷七

def _book_title(doc:Dict[str,Any])->str:
    """书名；book_title 缺失时回退 book_title_alt（繁体或异名）。"""
    return _clean_name(doc.get("book_title")) or _clean_name(doc.get("book_title_alt"))  #or天然短路：主书名有值就绝不去看别名

def _title_prefix(doc:Dict[str,Any])->str:
    """书名前的年号／年代前缀（地方志用）。"""
    return _clean_name(_extra(doc).get("title_prefix"))

def _chronology(doc:Dict[str,Any])->str:
    """编年体典籍的文字所属年月（选项）。"""
    return _s(_extra(doc).get("chronology"))  #用_s而不是_clean_name

def _title_block(doc:Dict[str,Any],index_text:str,chapter_style:str)->str:
    """题名块。"""
    book=_book_title(doc)
    if not book:
        return ""
    chapter=_clean_name(doc.get("chapter_title"))
    prefix=_title_prefix(doc)  #中圆点式把书名和篇名合成一个书名号
    if chapter_style=="middot":
        if chapter:
            return "%s《%s·%s》"%(prefix,book,chapter)
        return "%s《%s》%s"%(prefix,book,index_text) if index_text else "%s《%s》"%(prefix,book)  #连卷次都没有就只剩书名
    out="%s《%s》"%(prefix,book)
    if index_text:
        out+=index_text
    if chapter:  #篇名放在卷次之后，顺序固定
        out+="《%s》"%chapter
    return out

def _page_text(page:Any)->str:
    """页码："295" -> 第295页；"9a" -> 第9页a；"9-10" -> 第9—10页。"""
    token=_s(page)  #_s顺手把 int 型页码转成字符串
    if not token:
        return ""
    if token.startswith("第"):
        return token  #295页只缺第，补上就行
    if token.endswith("页") and token[:-1].isdigit():
        return "第"+token
    match=_PAGE_RANGE_RE.match(token)
    if match:  #用一字线而不是短横
        return "第%s—%s页"%(match.group(1),match.group(2))
    match=_PAGE_SUFFIX_RE.match(token)
    if match:
        return "第%s页%s"%(match.group(1),match.group(2))
    return "第%s页"%token  #保留原串，不认识的形制也不丢弃

def _raw_page(page:Any)->str:
    """GB/T 7714 的页码：8.5 要求纯阿拉伯数字，**不带第页**。"""
    token=_s(page)  #_s兼做类型兜底
    if not token:
        return ""
    token=token.strip("第").rstrip("页").strip()
    token=token.replace("-","—").replace("–","—").replace("~","—").replace("～","—")  #顺手去掉剥离后残留的空白
    return token

def _volume_count_text(value:Any)->str:
    """册次："上" -> 上册；"3" -> 第3册；"上册" 原样。"""
    token=_s(value)  #_s把 int 型册次也接住
    if not token:
        return ""
    if token in("上","中","下"):
        return token+"册"  #纯数字册次按规范写成第3册
    if token.isdigit():
        return "第%s册"%token
    return token

def _author_text(doc:Dict[str,Any],dynasty_style:str)->str:
    """著者串；dynasty_style="paren" 时前置（汉）圆括号。"""
    author=_join_authors(_clean_name(doc.get("author")))
    if not author:
        return ""
    dynasty=_clean_name(doc.get("author_dynasty"))
    if dynasty and dynasty_style=="paren":  #两个条件都要满足：有朝代
        return "（%s）%s"%(dynasty,author)
    return author

#出版年裸数字
def _year_plain(doc:Dict[str,Any])->str:
    """出版年裸数字串（1959 / "1959" -> "1959"）；没有则 ""。"""
    value=doc.get("publish_year")
    if isinstance(value,bool):  #bool 是 int 的子类
        return ""
    if isinstance(value,int):
        return str(value) if value>0 else ""
    token=_s(value).rstrip("年").strip()  #字符串形式先剥尾部的年，再清空白
    if token and token.isdigit():
        return token
    return token

#访问日期（中文形制）
def _date_text(value:Any)->str:
    """访问日期：2024-01-31 -> 2024年1月31日；无法解析则原样返回。"""
    token=_s(value)
    if not token:  #空值给空串
        return ""
    match=_DATE_RE.match(token)
    if match:
        return "%s年%d月%d日"%(match.group(1),int(match.group(2)),int(match.group(3)))  #拼成中文日期，%d而非%02d
    return token

#GB/T 引用日期
def _gbt_date_text(value:Any)->str:
    """GB/T 8.4.4.2 的引用日期：YYYY-MM-DD 格式。"""
    token=_s(value)
    if not token:  #没有日期就空串
        return ""
    match=_DATE_RE.match(token)
    if match:
        return "%s-%02d-%02d"%(match.group(1),int(match.group(2)),int(match.group(3)))
    match=_CN_DATE_RE.match(token)  #再试中文写法
    if match:
        return "%s-%02d-%02d"%(match.group(1),int(match.group(2)),int(match.group(3)))
    return token

#GB/T 出版年
def _gbt_year_text(doc:Dict[str,Any])->str:
    """GB/T 出版年串。"""
    year=_year_plain(doc)
    if not year:  #没有年份直接空串，8.4.3 整项省略
        return ""
    era=_s(_extra(doc).get("publish_year_era"))
    text=year+("(%s)"%era if era else "")
    if _extra(doc).get("publish_year_uncertain"):  #8.4.3.3 规定估计的出版年置于方括号内
        text="[%s]"%text
    return text

#历史学注释体例（页下注）

def _history_publish_segments(doc:Dict[str,Any])->List[str]:
    """历史学体例的版本／出版信息段。"""
    place=_s(doc.get("publisher_place"))  #出版地单独取，缺失时整段退化成只写出版者
    publisher=_s(doc.get("publisher"))
    edition=_s(doc.get("edition"))
    year=_year_plain(doc)
    if place and publisher:
        base="%s：%s"%(place,publisher)  #用中文全角冒号，与北京
    elif publisher:
        base=publisher
    else:
        base=place  #空串也是合法中间值
    segments:List[str]=[]
    if year:
        year_text="%s年"%year
        if edition:  #版本注不是必填项，缺了年份后面就直接收尾
            year_text+=edition
        segments.append("%s，%s"%(base,year_text) if base else year_text)
    else:  #没有年份，出版信息不能按原形制拼
        if base:
            segments.append(base)
        if edition:
            #无出版年时版本项独立成段（刻本一支
            segments.append(edition)
    return segments

#电子文献尾巴
def _history_tail(doc:Dict[str,Any])->str:
    """电子文献尾巴：，URL，访问日期（《历史研究》（五）电子文献）。"""
    url=_s(doc.get("url"))
    access=_date_text(doc.get("access_date"))  #访问日期走中文日期格式
    parts=[]
    if url:
        parts.append(url)
    if access:  #访问日期独立判断
        parts.append(access)
    return("，"+"，".join(parts)) if parts else ""

#历史学注释体例的入口
def format_history_note(
    doc:dict,
    *,
    page:Optional[str]=None,
    first:bool=True,
    dynasty_style:str="none",
    chapter_style:str="booktitle",
    index_style:str="arabic",
)->str:
    """历史学注释体例（页下注）单条。"""
    doc=_ensure_doc(doc)
    resolved_page=doc.get("page") if page is None else page
    index_text=_index_citation_text(doc,index_style)
    title_block=_title_block(doc,index_text,chapter_style)
    if not title_block:
        return ""  #没有可引之物就别往下拼了，硬凑出来的是，第295页

    head=title_block
    author=_author_text(doc,dynasty_style)
    if author:
        head="%s：%s"%(author,head)  #中文冒号是注释体例的责任者分隔符

    segments:List[str]=[head]

    #编年体典籍
    chronology=_chronology(doc)
    if chronology:
        segments.append(chronology)

    collator=_s(_extra(doc).get("collator"))  #点校者藏在extra里，不在顶层字段
    if collator:
        segments.append(collator)

    if first:
        segments.extend(_history_publish_segments(doc))  #出版项本身是一组（出版地、出版者、出版年）
        volume=_volume_count_text(doc.get("volume_count"))
        if volume:
            segments.append(volume)

    page_text=_page_text(resolved_page)  #页码用解析后的页码
    if page_text:
        segments.append(page_text)

    return "，".join(segments)+_history_tail(doc)+"。"  #尾缀（版本等）接在分段之后、句号之前

def _circled(number:int)->str:
    """注释序号：'1' 转成 '①'（《历史研究》：用①②③标识，每页单独排序）。"""
    if 1<=number<=len(_CIRCLED_1):  #1～50 走第一段圆圈数字
        return _CIRCLED_1[number-1]
    if number<=20+len(_CIRCLED_2):
        return _CIRCLED_2[number-21]
    if number<=35+len(_CIRCLED_3):  #㊱～㊿是最后一段
        return _CIRCLED_3[number-36]
    return "[%d]"%number

#同一文献判重键
def _repeat_key(doc:Dict[str,Any])->str:
    """判定同一文献用的键：优先 'id'，其次用 书名加卷次加篇名加出版年拼串。"""
    key=_s(doc.get("id"))
    if key:  #有id就直接返回，后面的字段拼装整段跳过
        return key
    parts=[
        _book_title(doc),
        _index_citation_text(doc,"arabic"),  #篇名要过clean_name，把书名号和空白剥掉
        _clean_name(doc.get("chapter_title")),
        _year_plain(doc),
    ]
    joined="|".join(p for p in parts if p)  #过滤掉空项再拼
    return joined

#批量页下注

#经典夹注

#其他题名信息
def _gbt_other_title_info(doc:Dict[str,Any],volume_style:str)->str:
    """8.2.3 其他题名信息：分卷书名、卷次、册次——这里用来挂卷次/册次/篇名。"""
    items:List[str]=[]
    number=_index_number(doc)  #卷次取归一化后的整数，取不到才退回原文串
    part=_part(doc)
    if number is not None:
        base="第%d卷"%number if volume_style=="di" else "卷%d"%number
        items.append((part+base) if part else base)  #有部名就把部名顶在卷次前，拼成魏志第1卷
    elif _s(doc.get("chapter_index_raw")):
        raw=_s(doc.get("chapter_index_raw"))
        items.append(raw if(not part or part in raw) else part+raw)
    volume=_volume_count_text(doc.get("volume_count"))  #册次单独成项，和卷次之间用, 分隔
    if volume:
        items.append(volume)
    chapter=_clean_name(doc.get("chapter_title"))
    if chapter:  #有篇名才进列表
        items.append(chapter)
    return ", ".join(items)

#出版项与占位
def _gbt_publish_item(doc:Dict[str,Any],placeholder:bool)->str:
    """8.4 出版项：出版地: 出版者, 出版年。"""
    place=_s(doc.get("publisher_place"))
    publisher=_s(doc.get("publisher"))  #出版者同理，两个都可能是空串
    year=_gbt_year_text(doc)
    if place and publisher:
        head="%s: %s"%(place,publisher)
    elif publisher:  #placeholder传假就只留出版者
        head=("[出版地不详]: %s"%publisher) if placeholder else publisher
    elif place:
        head=("%s: [出版者不详]"%place) if placeholder else place
    else:  #head置空串而不是None
        head=""
    if head and year:
        return "%s, %s"%(head,year)
    return head or year

#GB/T 古籍著录
def format_gbt7714_guji(
    doc:dict,
    *,
    page:Optional[str]=None,
    placeholder:bool=True,
    volume_style:str="di",
)->str:
    """GB/T 7714-2015《信息与文献 参考文献著录规则》的古籍著录（专著 '[M]'）。"""
    doc=_ensure_doc(doc)
    resolved_page=doc.get("page") if page is None else page
    book=_book_title(doc)
    if not book:
        return ""

    items:List[str]=[]  #items按著录顺序累加，最后统一用. 拼
    author=_author_text(doc,"none")
    if author:
        items.append(author)

    title=_title_prefix(doc)+book  #title_prefix带来的是（汉）这类前缀
    others=_gbt_other_title_info(doc,volume_style)
    if others:
        title="%s: %s"%(book,others)
    url=_s(doc.get("url"))  #有路径说明是电子资源
    title+="[M/OL]" if url else "[M]"
    items.append(title)

    collator=_s(_extra(doc).get("collator"))  #点校者放在extra里
    if collator:
        items.append(collator)

    edition=_s(doc.get("edition"))  #版本项单独取，和历史学体例把它并进出书信息不同
    if edition and not _FIRST_EDITION_RE.match(edition):
        items.append(edition)

    publish=_gbt_publish_item(doc,placeholder)  #出版项交给专门函数拼
    if publish:
        items.append(publish)

    text=". ".join(items)  #各项之间用. 拼接；注意句点后面还有一个空格
    raw_page=_raw_page(resolved_page)
    if raw_page:
        text+=": "+raw_page
    if url:  #有路径才补访问日期和路径
        access=_gbt_date_text(doc.get("access_date"))
        if access:
            text+="[%s]"%access
        text+=". "+url  #路径单独成项，前面用. 隔开
    return text+"."

#组合 / 展示

def format_guji_all(
    doc:dict,
    *,
    page:Optional[str]=None,
    dynasty_style:str="none",
    chapter_style:str="booktitle",
    index_style:str="arabic",
)->Dict[str,str]:
    """一次返回三种形态的引文串："""
    doc=_ensure_doc(doc)  #三个键共用同一份归一化输入
    return{
        "history":format_history_note(
            doc,page=page,first=True,
            dynasty_style=dynasty_style,chapter_style=chapter_style,index_style=index_style,  #三个风格参数原样透传，在这里不再加工
        ),
        "history_short":format_history_note(
            doc,page=page,first=False,
            dynasty_style=dynasty_style,chapter_style=chapter_style,index_style=index_style,  #两份历史学文本必须是同一套风格参数
        ),
        "gbt7714":format_gbt7714_guji(doc,page=page),
    }

#出处展示串
def describe_source(
    doc:dict,
    *,
    style:str="auto",
    with_author:bool=False,
    dynasty_style:str="none",
)->str:
    """人类可读的出处串，用于检索结果列表、引用溯源提示等展示场景。"""
    doc=_ensure_doc(doc)  #展示串也先归一化
    book=_book_title(doc)
    if not book:
        return ""
    chapter=_clean_name(doc.get("chapter_title"))  #篇名统一剥书名号
    index_text=_index_display_text(doc)
    prefix=_title_prefix(doc)

    if style=="middot" or(style=="auto" and not index_text):  #middot强制中圆点
        core=("%s《%s·%s》"%(prefix,book,chapter)) if chapter else("%s《%s》"%(prefix,book))
    elif style=="auto":
        core="%s《%s》"%(prefix,book)
        core+=index_text  #卷次直接接在书名号后、不带连接符
        if chapter:
            core+="《%s》"%chapter
    else:
        core="%s《%s》"%(prefix,book)  #booktitle和有卷次的auto开头一样
        if index_text:
            core+=index_text
        if chapter:
            core+="《%s》"%chapter  #篇名接在卷次之后

    if with_author:
        author=_author_text(doc,dynasty_style)
        if author:  #责任者取不到就原样返回正文，不输出None
            return "%s：%s"%(author,core)
    return core
