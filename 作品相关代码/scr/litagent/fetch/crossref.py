"""通过 Crossref 查询 DOI 题录。

Crossref 由 DOI 注册机构维护，刊名、卷期页和出版年适合用来校正其他
数据源。摘要以 JATS XML 返回，入库前会去掉标签。
"""
from __future__ import annotations

import html  #导入标准库 html，后面用它的 unescape 把实体写法还原成真实字符。
import re
from typing import Any, Dict, Iterable, List, Optional

from litagent.fetch.base import Source, register_source
from litagent.schema import (  #从 schema 导入文献模型、DOI 归一化函数与作者解析函数。
    Document,
    derive_document_status,
    normalize_doi,
    parse_authors,
)

#注释：这张表把 Crossref 的 type 取值翻译成本项目的 venue_type。
_TYPE_MAP={
    "journal-article":"journal",  #期刊论文映射为期刊类型 `journal`，这是最常见的一种。
    "journal-issue":"journal",  #期刊的一期也归入期刊类型，虽然它本身更像容器而非文章。
    "journal-volume":"journal",  #期刊的一卷同样归入期刊类型 `journal`。
    "journal":"journal",  #整个期刊本身也归入期刊类型，避免落到未知取值上。
    "proceedings-article":"conference",  #会议论文集里的论文映射为会议类型 `conference`。
    "proceedings":"conference",  #会议论文集整体也映射为会议类型 `conference`。
    "book-chapter":"chapter",  #书的章节映射为 `chapter`，与整本书区分开。
    "book-section":"chapter",  #书的节同样归入 `chapter`，保持与章一致的粒度。
    "book-part":"chapter",  #书的部也归入 `chapter`，不让它变成未知类型。
    "reference-entry":"chapter",  #参考工具书条目如百科词条，归入 `chapter`。
    "book":"book",  #整本书映射为 `book` 类型。
    "monograph":"book",  #专著映射为 `book`，因为引用格式与书一致。
    "edited-book":"book",  #编著同样映射为 `book`，与普通图书保持一致。
    "reference-book":"book",  #参考书与工具书映射为 `book`。
    "textbook":"book",  #教材也映射为 `book`，把学生用书归入图书类。
    "dissertation":"thesis",  #学位论文映射为 `thesis`，引用时需要额外标注学位信息。
    "report":"report",  #技术报告映射为 `report` 类型。
    "report-series":"report",  #报告系列也映射为 `report`。
    "standard":"standard",  #标准文献映射为 `standard` 类型。
    "standard-series":"standard",  #标准系列同样映射为 `standard`。
    "patent":"patent",  #专利文献同样映射为 `patent` 类型。
    "dataset":"dataset",  #数据集这类科研产出映射为 `dataset`。
    "posted-content":"preprint",  #预印本平台上的帖子映射为 `preprint`。
    "peer-review":"journal",  #同行评审报告归入 `journal`，省得产生大量未知类型。
    "component":"unknown",  #组件如图表附件无法归类，映射为 `unknown`。
    "grant":"report",  #资助项目记录映射为 `report`，因为形式上是报告类文档。
    "other":"unknown",  #其它类型统一映射为 `unknown`。
}  #字典定义结束，这张映射表到此完整。

_TAG_RE=re.compile(r"<[^>]+>")  #预编译正则 `_TAG_RE`，匹配一对尖括号及其内部内容，用来整段删掉标签。
_WS_RE=re.compile(r"\s+")  #预编译空白正则 `_WS_RE`：反斜杠 s 加号表示一个或多个空白字符。


def strip_jats(text:Optional[str])->Optional[str]:
    """剥离 JATS/HTML 标签并还原实体，得到纯文本摘要。

    Crossref 的摘要形如 ``<jats:p>...</jats:p>``，其中还夹杂 ``&lt;`` 等实体。
    """
    if not text:  #边界判断：传入空串或 None 时说明没有摘要可处理。
        return None  #直接返回 None，避免后面的正则对空值报错。
    #注释：先把标题与段落结束标记换成换行，保留原有分段。
    cleaned=re.sub(r"</jats:(?:p|title|sec|abstract)>","\n",text,flags=re.I)
    cleaned=re.sub(r"<jats:title[^>]*>","\n",cleaned,flags=re.I)
    cleaned=_TAG_RE.sub(" ",cleaned)  #用标签正则把剩下的所有标签替成空格，防止相邻单词被粘在一起。
    cleaned=html.unescape(cleaned)
    cleaned=_WS_RE.sub(" ",cleaned).strip()  #把连续空白压成一个空格并去掉首尾空白，得到规范文本。
    return cleaned or None


def _unescape(text:Optional[str])->Optional[str]:
    """反转义 HTML 实体并压缩空白。

    Crossref 的字段里经常残留 ``&amp;`` 之类的实体（如
    "Information &amp; Knowledge Management"），不处理会原样进入引用条目。
    """
    if not text:  #边界判断：字段为空或缺失时不再往下处理。
        return None
    value=html.unescape(str(text))
    value=_WS_RE.sub(" ",value).strip()  #用空白正则压缩内部空白，再去掉首尾空白。
    return value or None


def _first(value:Any)->Optional[str]:
    """Crossref 里很多字段是数组，取第一个非空元素。"""
    if value is None:
        return None  #值为 None 时直接返回，跳过后面所有分支。
    if isinstance(value,list):
        for item in value:  #遍历列表中的每个元素，遇到空值就继续往后找。
            if item:
                return str(item).strip() or None
        return None
    text=str(value).strip()  #不是列表时当标量处理，转字符串并去掉首尾空白。
    return text or None


def _date_parts_to_year(*candidates:Any)->Optional[int]:
    """从 ``{"date-parts": [[2017, 6, 12]]}`` 里取年份。"""
    for candidate in candidates:
        if not isinstance(candidate,dict):  #判断该字段是否真的是字典，不是就跳过，避免类型错误。
            continue  #跳过这个候选字段，继续看下一个。
        parts=candidate.get("date-parts")  #取出日期数组字段，它一般是嵌套两层的列表。
        if isinstance(parts,list) and parts and isinstance(parts[0],list) and parts[0]:  #逐层校验：必须是非空列表，且第一个元素也是非空列表，否则跳过。
            try:  #开始异常保护块，里面可能因为脏数据而出错。
                year=int(parts[0][0])  #取最内层第一个数字并转成整数，也就是年份，月日一律丢弃。
            except(TypeError,ValueError):  #捕获类型错误与取值错误，上游给文字或 None 时不该让整个解析失败。
                continue  #这一项解析失败，跳过并继续看下一个候选字段。
            if 1000<=year<=2200:  #合理性校验：只接受一千到两千两百年之间，滤掉写错的年份。
                return year  #通过校验立即返回，后面的候选字段不再尝试。
    return None  #所有候选都没给出合法年份时返回 None，让上层字段留空。


def _detect_retraction(item:Dict[str,Any])->tuple[bool,Optional[str]]:
    """检测一条 Crossref 记录是否被撤稿，返回 (是否撤稿, 撤稿说明)。

    Crossref 是 DOI 注册机构，撤稿信息最权威。撤稿信号有三处，命中任一处即判撤稿：
    1. ``assertion`` 数组里出现 retraction/retracted 字样；
    2. ``update-to`` 数组里出现 retraction 类型的更新；
    3. ``relation`` 字典里出现 has-retraction / has-retracted-article。
    """
    for assertion in item.get("assertion") or[]:
        if not isinstance(assertion,dict):
            continue
        blob=" ".join(
            str(assertion.get(k) or "") for k in("name","value","label")
        ).lower()
        if "retract" in blob:
            note=assertion.get("label") or assertion.get("value") or "Retraction"
            return True,str(note)
    for upd in item.get("update-to") or[]:
        if not isinstance(upd,dict):
            continue
        if "retract" in str(upd.get("type") or "").lower():
            return True,str(upd.get("label") or "Retraction (update-to)")
    relation=item.get("relation") or{}
    if isinstance(relation,dict):
        for key in("has-retraction","has-retracted-article","has-retraction-event"):
            if relation.get(key):
                return True,"Retraction (relation)"
    return False,None


@register_source  #装饰器：把这个类注册进数据源表，命令行按名字就能找到它。A
class CrossrefSource(Source):  #定义 Crossref 数据源类，继承基类以获得限流、缓存与重试能力。
    """Crossref REST API 客户端。"""

    name="crossref"  #类属性 `name` 取值为 crossref，作为数据源短名与缓存目录名。
    default_rate=4.0  #限流就写在这一行：默认每秒最多四次请求，对应 Crossref 的公开上限。
    supports_search=True  #声明本数据源支持关键词检索。
    supports_doi=True  #声明本数据源支持按 DOI 精确查询。
    base_url="https://api.crossref.org"  #接口根地址，后面拼上 works 组成完整请求地址。

    def rate(self)->float:  #重写 `rate` 方法，返回本数据源每秒请求上限。
        return self.settings.crossref_rps  #从配置读取，默认四点零；要提速改环境变量即可，不必动代码。

    def user_agent(self)->str:  #重写 `user_agent`，返回请求头里的身份字符串。
        """Crossref 官方要求 UA 里带联系方式，否则可能被降级或封禁。"""
        if self.settings.contact_email:  #判断配置里是否填写了联系邮箱。
            return "lit-agent/0.1 (mailto:%s)"%self.settings.contact_email
        return "lit-agent/0.1 (mailto:unconfigured@example.com)"  #没配邮箱时用占位邮箱兜底，格式仍合法，只是不享受优待。

    #注释分隔线，后面是解析相关的方法。
    #注释：下面这段负责把接口返回的 JSON 转成 Document。
    #这一条注释分隔线结束，解析方法部分写完了。
    @classmethod  #类方法装饰器：解析逻辑不依赖实例状态，写成类方法便于单独测试。
    def _to_document(cls,item:Dict[str,Any])->Optional[Document]:  #核心映射函数：接收一条 JSON 记录，返回 Document，字段不够时返回 None。
        title=_first(item.get("title")) or _first(item.get("short-title"))
        if not title:
            return None  #标题为空的记录直接丢弃，因为文献没有题名无法使用。
        title=_WS_RE.sub(" ",html.unescape(title)).strip()  #标题同样要反转义并压空白，否则与号等实体会进入引用条目。

        doi=normalize_doi(item.get("DOI"))  #调用 `normalize_doi` 把 DOI 统一成规范形式，便于跨源去重合并。

        #注释小标题：下面开始处理作者。
        #注释：Crossref 直接给出结构化的姓与名，是最可靠的作者信息来源。
        #注释：一定要原样用字典层层传下去，不要拼成一个字符串。
        #注释：拼成字符串再解析会丢结构，还可能被按词序的规则拆错。
        authors:List[Dict[str,str]]=[]
        for raw_author in item.get("author") or[]:  #遍历作者数组；字段缺失时用空列表兜底，避免迭代 None 报错。
            family=(raw_author.get("family") or "").strip()  #取姓；缺失时用空串兜底再去空白，保证后面判断不出错。
            given=(raw_author.get("given") or "").strip()  #取名，含名字与中间名缩写，同样兜底并去空白。
            name=(raw_author.get("name") or "").strip()  #取整体名称字段；机构作者没有姓和名，只有这一个名称。
            if family or given:  #只要姓或名有一个非空，就按自然人作者处理。
                authors.append({
                    "family":family,  #存姓，保持结构化，后面按姓氏排序或缩写时直接可用。
                    "given":given,  #存名，与姓分开保存，缩写时才不会把姓也一起缩写掉。
                    "raw":("%s %s"%(given,family)).strip(),  #另存一个原文串，由名加空格加姓拼成，供展示与比对使用。
                })  #字典与追加调用一起收尾。
            elif name:  #否则若整体名称非空，说明这是机构作者。
                #注释：机构作者如某某协作组织，没有姓和名这两个字段。
                authors.append({"family":"","given":"","raw":name})  #机构作者姓与名留空，只把整体名称放进原文串，避免被错误拆分。

        container=_first(item.get("container-title")) or _first(  #取期刊或会议名称，先试完整名称。
            item.get("short-container-title"))  #完整名称取不到时退回简称，这是上一行调用的续行。
        if not container:
            #注释：会议论文的会议名称常常放在 event 字段里。
            event=item.get("event") or{}  #取出 event 字典；缺失时用空字典兜底。
            container=(event.get("name") or "").strip() or None
        #注释：名称里经常夹着 HTML 实体，例如带与号的刊名。
        #注释：不反转义就会原样进入引用条目，所以这里必须同样处理。
        container=_unescape(container)

        publisher=_unescape((item.get("publisher") or "").strip() or None)  #取出版者并反转义；这是 Crossref 最权威的字段之一，写引用时必备。
        year=_date_parts_to_year(  #开始取出版年，传入多个日期字段作为候选。
            item.get("issued"),item.get("published-print"),  #候选一与候选二：正式发布日期优先于印刷版日期。
            item.get("published-online"),item.get("created"),  #候选三与候选四：网络出版日期，以及记录创建日期作最后兜底。
        )

        retracted,retraction_note=_detect_retraction(item)  #检测撤稿：Crossref 是撤稿信息最权威的 DOI 注册源。

        doc=Document(  #开始构造 Document 对象，把清洗好的字段一个个塞进去。
            id=Document.make_id("crossref",None,doi,title),  #生成稳定 ID：优先用 DOI，没有才退回标题哈希；第二个参数表示没有源内编号。
            doi=doi,  #写入归一化后的 DOI，跨数据源合并就靠它。
            title=title,  #写入已经清洗过的标题。
            authors=parse_authors(authors),
            year=year,  #写入年份字段，取不到时这里就是 None。
            venue=container,  #写入刊名或会议名。
            venue_type=_TYPE_MAP.get((item.get("type") or "").lower(),"unknown"),  #查映射表把 Crossref 类型翻成本项目类型，缺失或没登记的一律给未知。
            volume=_first(item.get("volume")),  #卷号取数组第一项，Crossref 有时把卷号写成数组。
            issue=_first(item.get("issue")),  #期号同样取数组第一项。
            pages=_first(item.get("page")),  #页码取第一项，通常是起止页连写的字符串。
            publisher=publisher,  #写入出版者名称，这是 Crossref 最权威的字段之一。
            url=(item.get("URL") or "").strip() or None,  #写入落地页地址并去空白，空串写成 None。
            language=(item.get("language") or "").strip() or(  #语言字段先试接口给的，取不到就进入下一行的兜底判断。
                "zh" if any("\u4e00"<=ch<="\u9fff" for ch in title) else "en"  #标题里出现任意一个中日韩汉字就判为中文，否则判为英文。
            ),  #兜底表达式到此收尾，语言参数赋值完成。
            abstract=strip_jats(_first(item.get("abstract"))),  #摘要先取第一项再用剥标签函数处理，得到可读纯文本。
            keywords=[s for s in(item.get("subject") or[]) if s],  #主题词列表过滤掉空值后作为关键词。
            source="crossref",  #标记来源为 crossref，便于后续按源统计与合并。
            source_id=(item.get("DOI") or None),  #源内编号就用原始 DOI。
            is_oa=False,  #默认标记为非开放获取，因为 Crossref 不保证这点，要靠别的源补。
            citation_count=item.get("is-referenced-by-count"),  #被引次数直接取 Crossref 的计数字段。
            extra={  #开始写附加信息字典，存放不进入标准字段的原始数据。
                "type":item.get("type"),  #保留 Crossref 原始类型字符串，方便排查映射是否合理。
                "is_retracted":retracted,  #撤稿标记：与 OpenAlex 同键，Document.retracted 从它派生。
                "status":derive_document_status(retracted,item.get("type")),  #状态：retracted/preprint/erratum/active。
                "retraction_note":retraction_note,  #撤稿说明，未撤稿时为 None。
                "issn":item.get("ISSN") or[],  #保留期刊的国际标准刊号列表，缺失时给空列表。
                "isbn":_first(item.get("ISBN")),  #书的国际标准书号取第一个，因为有些记录给多个版本。
                "reference_count":item.get("reference-count"),  #保留参考文献条数，可用于判断文献的综述性质。
                "license":[  #许可证地址列表，只保留带网址的那几条。
                    (lic or{}).get("URL")  #取每一项里的网址；用空字典兜底，防止列表里混进 None 报错。
                    for lic in(item.get("license") or[])  #遍历许可证数组，字段缺失时用空列表兜底。
                    if(lic or{}).get("URL")  #过滤条件：只保留网址非空的项。
                ][:3],  #列表切片只留前三项，防止异常记录把字段撑得过大。
                "link":[  #开始整理全文链接列表，供后面找 PDF 直链用。
                    {"url":(lnk or{}).get("URL"),  #每条链接只保留网址与内容类型两个键。
                     "content_type":(lnk or{}).get("content-type")}  #内容类型常见为 PDF 或 XML，用来判断能不能直接下全文。
                    for lnk in(item.get("link") or[])  #遍历链接数组，字段缺失时同样给空列表。
                ][:5],  #最多保留五条链接。
            },  #附加信息字典的内容到这里结束，稍后随文献对象一起保存。
        )  #文献对象的构造调用到此结束，得到一个完整对象。
        if doc.extra.get("isbn"):  #判断附加信息里是否真有国际标准书号。
            doc.isbn=doc.extra["isbn"]
        return doc

    #注释分隔线，下面是接口实现部分。
    #注释小标题：检索、按 DOI 查询与 PDF 链接。
    #第二条分隔线结束，接口实现部分正式开始。
    def search(self,query:str,*,limit:int=20,offset:int=0,  #定义检索方法；星号表示后面的参数必须用关键字传，避免传参顺序错。
               year_from:Optional[int]=None,year_to:Optional[int]=None,  #年份区间参数，用来拼日期过滤条件。
               venue_type:Optional[str]=None,open_access_only:bool=False,  #文献类型与只看开放获取两个参数，后者 Crossref 用不上但保留以统一接口。
               **filters:Any)->List[Document]:  #多余的关键字参数全部收进一个字典，接受但忽略，保证与基类签名兼容。
        """关键词检索。Crossref 的 ``offset`` 最大 10000。"""
        rows=max(1,min(int(limit),1000))
        params:Dict[str,Any]={  #开始构造查询参数字典，最终会拼成地址里的问号参数。
            "query":query,  #关键词参数，Crossref 会按相关性排序返回结果。
            "rows":rows,  #每页返回的条数，已经夹在合法区间内。
            "offset":max(0,int(offset)),  #偏移量，负数一律归零。
        }  #基础查询参数字典构造完成，下面再追加过滤条件。
        filter_parts:List[str]=[]
        if year_from:
            filter_parts.append("from-pub-date:%d-01-01"%int(year_from))  #拼出起始日期条件，月份固定为一月一日；这里用的是出版日期。
        if year_to:
            filter_parts.append("until-pub-date:%d-12-31"%int(year_to))  #拼出结束日期条件，月份固定为十二月三十一日，凑成闭区间。
        if venue_type:
            crossref_types=[k for k,v in _TYPE_MAP.items() if v==venue_type]  #在映射表里反向查找，把本项目类型换回所有对应的 Crossref 类型名。
            if crossref_types:
                filter_parts.append("type:"+",".join(crossref_types))
        if filter_parts:
            params["filter"]=",".join(filter_parts)  #多个条件用逗号连接成单个过滤参数，这是 Crossref 特有的约定。
        params.update(self.polite_params())

        payload=self.client.get_json(self.base_url+"/works",params=params)  #拼出检索地址并请求 JSON，限流、缓存与重试都由这个客户端负责。
        if not payload:
            return[]  #网络失败或空响应时返回空列表，而不是抛异常中断整批任务。
        items=((payload.get("message") or{}).get("items")) or[]  #层层取值：结果在 message 的 items 里，任何一层缺失都用空列表兜底。
        return self.parse_results(items)  #交给批量解析方法逐条转换。

    def parse_results(self,items:Iterable[Dict[str,Any]])->List[Document]:
        documents:List[Document]=[]
        for item in items:
            doc=self._to_document(item)
            if doc is not None:
                documents.append(doc)  #成功才追加，缺标题的脏记录自然被过滤掉。
        return documents

    def by_doi(self,doi:str)->Optional[Document]:  #定义按 DOI 精确取题录的方法。
        """按 DOI 精确获取题录。"""
        norm=normalize_doi(doi)  #先做 DOI 归一化，去掉常见前缀写法，避免请求到错误地址。
        if not norm:
            return None  #DOI 不合法就直接返回 None，省掉一次无效网络请求。
        params=dict(self.polite_params())  #复制一份礼貌参数，用字典包一层是为了不修改共享的原始字典。
        payload=self.client.get_json(  #发起请求，完整地址在下一行拼接。
            "%s/works/%s"%(self.base_url,norm),params=params,allow_404=True  #地址是根地址加 works 再加归一化 DOI；允许查不到，因为 404 属于正常结果。
        )  #这一次请求调用到此结束。
        if not payload or not isinstance(payload,dict):
            return None  #响应不可用时直接返回 None，不再往下取字段。
        message=payload.get("message")
        if not isinstance(message,dict):  #再确认 message 是字典，防止接口返回错误说明时崩溃。
            return None  #不是字典也返回 None，避免对错误信息做字段映射。
        return self._to_document(message)

    def by_id(self,source_id:str)->Optional[Document]:  #实现按源内编号查询的接口。
        return self.by_doi(source_id)  #Crossref 的源内编号就是 DOI，所以直接转调即可。

    def pdf_url(self,doc:Document)->Optional[str]:  #定义取 PDF 直链的方法，输入一条文献记录。
        """从 ``link`` 里挑一个 ``application/pdf`` 的直链。"""
        for link in(doc.extra or{}).get("link") or[]:  #遍历附加信息里保存的链接列表，两层兜底保证没有链接也不报错。
            url=link.get("url")  #取出这一条链接的地址。
            content_type=(link.get("content_type") or "").lower()  #内容类型转成小写，后面做包含判断就不用管大小写。
            if url and("pdf" in content_type or str(url).lower().endswith(".pdf")):  #双重判断：类型里含 pdf，或地址以点 p d f 结尾，两种都算 PDF 直链。
                return str(url)  #命中就立即返回第一个 PDF 链接。
        return None  #遍历完没有命中的就返回 None，表示拿不到直链。


__all__=["CrossrefSource","strip_jats"]  #导出清单：只公开这两个名字，其余函数都算内部实现细节。
