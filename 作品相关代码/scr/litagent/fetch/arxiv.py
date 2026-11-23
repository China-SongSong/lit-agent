"""查询 arXiv 预印本，并提供开放的 PDF 地址。

官方要求请求间隔至少 3 秒，所以客户端把速率限制为 0.33 QPS。接口返回
Atom XML，journal_ref 可用来补齐预印本正式发表的期刊信息。
"""
from __future__ import (
    annotations,  #导入未来特性，让类型注解变成延迟求值的字符串，低版本解释器也能写新语法。
)

import re
import xml.etree.ElementTree as ET  #导入标准库 XML 解析器并简写为 ET，用来解析 arXiv 返回的 Atom 文本。
from typing import Any, Dict, List, Optional

from litagent.fetch.base import Source, register_source
from litagent.schema import (  #导入统一的文献模型，以及规范化 DOI、拆分作者字符串两个工具函数。
    Document,
    normalize_doi,
    parse_authors,
)

_ATOM="{http://www.w3.org/2005/Atom}"  #定义 Atom 命名空间前缀，查找元素必须带上这个花括号前缀才找得到。
_ARXIV="{http://arxiv.org/schemas/atom}"  #定义 arXiv 自己的命名空间前缀，用于查找 doi、journal_ref 等扩展字段。

#注释：下面这张表把 arXiv 的分类前缀映射成人类可读的学科全名。
#注释：默认都算预印本，但若 journal_ref 存在就改判为期刊论文。
_CATEGORY_LABELS={  #开始定义分类字典，键是 arXiv 分类前缀，值是学科名。
    "cs":"Computer Science",  #键 cs 映射为计算机科学，检索结果展示时用它当学科标签。
    "math":"Mathematics",  #键 math 映射为数学，只是给人看的标签，不参与检索过滤。
    "physics":"Physics",  #键 physics 映射为物理学，覆盖 arXiv 上数量最多的那批论文。
    "stat":"Statistics",  #键 stat 映射为统计学，注意它是 statistics 的缩写而不是 state。
    "q-bio":"Quantitative Biology",  #键 q-bio 映射为定量生物学，前缀里的横线要原样保留。
    "q-fin":"Quantitative Finance",  #键 q-fin 映射为定量金融。
    "econ":"Economics",  #键 econ 映射为经济学，对应经济类的预印本论文。
    "eess":"Electrical Engineering and Systems Science",  #键 eess 映射为电气工程与系统科学，这是较晚新增的分类前缀。
}  #字典定义结束；遇到没收录的新分类不会报错，只是取不到全名。

#注释：说明下面两个正则的分工，是从 journal_ref 里提取刊名与年份。
#注释：空注释行，用来在源码里分段，让下面这段长说明更好读。
#注释：举例说明 journal_ref 的典型样子，是刊名加卷号再加年份。
#注释：总结格式规律，同时提醒各家期刊写法自由，不能一刀切。
#注释：继续列举畸形情况，有的没有文章号，有的干脆只写刊名。
#注释：再一个空注释行，把历史教训和现行做法分隔开。
#注释：回顾早期版本用一条非贪婪正则一次搞定的写法，那正是要避开的坑。
#注释：贴出当年那条问题正则原文，重点看里面的非贪婪量词与命名分组。
#注释：点明病根，非贪婪的星号问号会尽量少匹配，导致长刊名被切短。
#注释：说明后果，Physical Review D 只剩 Physical，引用信息少了一大半。
#注释：空注释行，再次分段，下面开始讲现在采用的正确做法。
#注释：说明现在改成的三步法，先摘年份，再取逗号前，最后删末尾卷号。
#注释：强调每步都贪心且语义明确，所以多词刊名不会被误截断。
_JOURNAL_REF_YEAR_RE=re.compile(
    r"\((?P<year>(?:19|20)\d{2})\)"
    r"(?:\s*[,;:]?\s*(?P<pages>\d+(?:\s*[-–]\s*\d+)?))?\s*$"
)
_JOURNAL_REF_YEAR_PAGES_RE=re.compile(
    r"[,;]\s*(?P<year>(?:19|20)\d{2})"
    r"(?:\s*[,;:]?\s*(?P<pages>\d+(?:\s*[-–]\s*\d+)?))?\s*$"
)
_JOURNAL_REF_TRAILING_VOLUME_RE=re.compile(r"[\s,]\d+\s*$")  #编译卷号正则，匹配串尾紧随的一串数字即卷号；反斜杠 s 表示空白，反斜杠 d 表示数字。

_WS_RE=re.compile(r"\s+")  #编译空白正则，把连续的空白字符一次替换成单个空格，避免摘要里换行错乱。


def _text(node:Optional[ET.Element])->Optional[str]:  #定义内部小函数，接收一个 XML 节点，安全地取出它的文本并返回清洗后的字符串。
    if node is None or node.text is None:  #先判断节点为空或节点没有文本，这两种情况都不能直接取值，否则会抛异常。
        return None  #上面任一情况成立就返回 None，调用方拿到 None 时走默认分支。
    value=_WS_RE.sub(" ",node.text).strip()
    return value or None

def parse_journal_ref(journal_ref:Optional[str])->"tuple[Optional[str], Optional[int]]":
    """从 ``journal_ref`` 解析出 ``(刊名, 年份)``。

    例：``"Physical Review D 90, 123456 (2014)"`` -> ``("Physical Review D", 2014)``
    解析不出刊名时返回 ``(原始字符串, 年份)``，宁可留着原文也不要丢信息。
    """
    if not journal_ref:  #入口先防御空值：传入 None 或空字符串时直接短路返回，不做任何正则匹配。
        return None,None  #空输入返回两个 None，表示刊名和年份都没解析出来。
    ref=journal_ref.strip()

    year:Optional[int]=None
    year_match=(
        _JOURNAL_REF_YEAR_RE.search(ref)
        or _JOURNAL_REF_YEAR_PAGES_RE.search(ref)
    )
    if year_match:
        year=int(year_match.group("year"))
        ref=ref[:year_match.start()].strip()

    #注释：解释切分依据，第一个逗号之前是刊名加卷号，逗号之后通常是文章号。
    head=ref.split(",")[0].strip()
    #注释：举例说明下一步要删掉末尾卷号，用例子展示删除前后的样子。
    #注释：提醒只删空白和逗号，不要连末尾的句点一起删掉。
    #注释：举例说缩写刊名末尾那个点是缩写标记，删掉就不成话了。
    head=_JOURNAL_REF_TRAILING_VOLUME_RE.sub("",head).strip().rstrip(",")  #用卷号正则删掉末尾数字，再清掉空白和残余逗号；这步贪心，所以长刊名安全。
    head=re.sub(r"\s+"," ",head).strip()  #再一次把内部连续空白收成单个空格并去首尾，防止刊名里混进多余空格。
    return(head or journal_ref.strip()),year


@register_source  #装饰器注册：类定义完成时自动把 ArxivSource 登记进全局数据源表。
class ArxivSource(Source):  #定义 ArxivSource 类并继承 Source 基类，从而获得限流、缓存等公共能力。
    """arXiv Atom API 客户端。"""

    name="arxiv"  #类属性 name 是数据源唯一标识，会写进文档的 source 字段用来标记出处。
    default_rate=0.33  #默认速率设为 0.33 次每秒，约等于 3 秒一次，这正是官方要求的间隔下限。
    supports_search=True  #声明本数据源支持关键词检索，调度器看到它才会把检索任务分过来。
    supports_doi=False  #声明不支持按 DOI 取单篇，arXiv 用自己的一套编号，只能按编号取。
    base_url="https://export.arxiv.org/api"  #接口根地址用 export 域名，因为它专门给程序调用，比主站更稳定。

    def rate(self)->float:
        return min(self.settings.arxiv_rps,1.0)  #取配置速率与 1.0 中较小者，即使用户配得再高也不会超过每秒一次。

    #注释分隔线，把解析相关的内部方法和其他代码隔开，纯装饰不影响运行。
    #注释：下面这一组方法都负责解析，小标题就叫解析。
    #第二条注释分隔线，与上一条配对，围出这个小标题。
    @staticmethod  #静态方法装饰器，说明这个函数不访问实例也不访问类，是纯函数。
    def _short_id(raw_id:Optional[str])->Optional[str]:
        """``http://arxiv.org/abs/2101.00001v2`` -> ``2101.00001``（去掉版本号）。"""
        if not raw_id:
            return None  #空输入返回 None，交给调用方去走各自的兜底逻辑。
        tail=raw_id.rstrip("/").rsplit("/",1)[-1]  #先去掉末尾斜杠，再从最后一个斜杠处切一刀取后半段，得到带版本号的编号。
        return re.sub(r"v\d+$","",tail)  #用正则删掉末尾的版本号标记，只留下主编号，这样同一篇的不同版本会合并。

    @classmethod  #类方法装饰器，第一个参数是类本身，方便在内部调用同类其他方法。
    def _to_document(cls,entry:ET.Element)->Optional[Document]:
        title=_text(entry.find(_ATOM+"title"))  #用 find 找 Atom 命名空间下的 title 节点，再借助文本函数取回清洗过的标题。
        if not title:  #标题是必填项，取不到标题的条目直接判为无效，避免库里出现空壳记录。
            return None
        title=_WS_RE.sub(" ",title).replace("\n"," ").strip()  #二次清洗标题：连续空白收成单空格，再把换行换成空格，最后去首尾空白。

        arxiv_id=cls._short_id(_text(entry.find(_ATOM+"id")))  #取 id 节点文本并交给编号压缩函数，得到干净的 arXiv 编号。
        summary=_text(entry.find(_ATOM+"summary"))  #取 summary 节点文本作为摘要，它对应 arXiv 页面上的 Abstract。
        published=_text(entry.find(_ATOM+"published")) or ""  #取首发时间字符串；取不到时用空字符串兜底，免得后面切片报错。
        year:Optional[int]=None
        if len(published)>=4 and published[:4].isdigit():  #判断字符串是否够长且前四位全是数字，这样切片取年份才安全。
            year=int(published[:4])  #把前四位转成整数当成年份，标准时间串的开头正好是四位年份。

        authors=parse_authors(  #调用作者规范化函数，把作者名列表转成规范对象，这里先写函数名准备参数。
            [name for name in(  #开始写列表推导式，逐个遍历下面生成器产出的作者名。
                _text(a.find(_ATOM+"name")) for a in entry.findall(_ATOM+"author")  #找出所有 author 节点，逐个取 name 子节点文本，拼成作者名序列。
            ) if name]  #只有名字非空的才保留，过滤掉缺 name 的残缺作者节点。
        )  #收尾括号，作者列表构造完成并交给规范化函数处理。

        doi=normalize_doi(_text(entry.find(_ARXIV+"doi")))  #从 arXiv 扩展命名空间取 doi，再规范化大小写与前缀，统一存进 doi。
        journal_ref=_text(entry.find(_ARXIV+"journal_ref"))  #取 journal_ref 文本，它记录这篇预印本后来正式发表在哪本刊。
        comment=_text(entry.find(_ARXIV+"comment"))  #取 comment 文本，作者常在这里写页数、会议、代码链接等补充信息。

        primary=entry.find(_ARXIV+"primary_category")  #找到主分类节点存进 primary，它是单值节点，可能不存在所以要判空。
        primary_category=primary.get("term") if primary is not None else None  #用条件表达式取值：节点存在就读 term 属性，不存在就置 None，避免报错。
        categories=[  #开始构建分类列表，下面用推导式从所有 category 节点里取代码。
            c.get("term") for c in entry.findall(_ATOM+"category") if c.get("term")  #遍历全部 category 节点取 term 属性，并过滤掉没有该属性的节点。
        ]  #列表推导式收尾，列表里是一串形如 cs.CL 的分类代码。

        #注释：说明下一段逻辑要从 journal_ref 反推正式发表的刊名与年份。
        venue:Optional[str]=None
        venue_type="preprint"  #默认类型是预印本，arXiv 上绝大多数条目都属于这一类。
        if journal_ref:  #只有当 journal_ref 有值时才进入改写分支，说明这篇已经正式发表。
            venue,journal_year=parse_journal_ref(journal_ref)
            #注释：说明为何要改年份与类型，已发表的按期刊论文著录更符合引用习惯。
            if journal_year:  #只有解析出年份才覆盖，避免用空值把原来的首发年份抹掉。
                year=journal_year
            venue_type="journal"  #类型改判为期刊论文，下游就能据此区分预印本与已发表文献。

        #注释：下面这段循环要从 link 节点里挑出摘要页和 PDF 直链两个地址。
        abs_url=None  #摘要页地址先置空，等循环里找到合适的节点再填进去。
        pdf_url=None  #PDF 直链同样先置空，找不到时后面会用编号拼一个默认地址。
        for link in entry.findall(_ATOM+"link"):  #遍历条目里所有 link 节点，一个条目通常同时带摘要页和 PDF 两个链接。
            href=link.get("href")  #取 href 属性，也就是链接真正指向的地址。
            rel=link.get("rel")  #取 rel 属性，它标明链接用途，摘要页通常标记为 alternate。
            title_attr=(link.get("title") or "").lower()  #取 title 属性并转小写；先用空字符串兜底，防止属性缺失时报错。
            if rel=="alternate" and href and abs_url is None:
                abs_url=href
            if title_attr=="pdf" and href:  #判断是不是 PDF 链接：arXiv 把 PDF 链接的 title 属性写成 pdf。
                pdf_url=href  #记录 PDF 直链，它指向可下载的全文，是溯源功能最关键的原料。
        if not pdf_url and arxiv_id:  #万一 feed 里没给 PDF 链接，只要编号有效就自己拼一个，保证字段不空。
            pdf_url="https://arxiv.org/pdf/%s"%arxiv_id  #用百分号占位符把编号填进地址模板，这是官方可预测的下载地址。
        if not abs_url and arxiv_id:  #摘要页地址同理兜底，缺了就按编号规则拼出来。
            abs_url="https://arxiv.org/abs/%s"%arxiv_id  #拼出摘要页地址，用户点进去能看到摘要与 PDF 入口。

        doc=Document(  #开始构造统一的文档对象，下面这些字段一次性传给构造函数。
            id=Document.make_id("arxiv",arxiv_id,doi,title),  #生成全局唯一标识：用来源、编号、DOI、标题一起算，缺哪个会自动跳过。
            doi=doi,  #把规范化后的 DOI 写进文档，没有就写 None。
            title=title,  #写入标题，前面已经做过空白与换行清洗。
            authors=authors,  #写入作者列表，元素是规范化后的作者对象。
            year=year,  #写入年份，可能来自首发时间，也可能来自正式发表年。
            venue=venue,  #写入刊名，预印本状态时它还是 None。
            venue_type=venue_type,  #写入文献类型，取值是预印本或期刊论文。
            url=abs_url,  #把摘要页地址作为对外的规范链接。
            language="en",  #arXiv 上的论文默认按英文著录，语言字段直接写死。
            abstract=summary,  #写入摘要正文，供后续做关键词打分与语义检索使用。
            keywords=categories,  # type: ignore[reportArgumentType]  #把分类代码当关键词写入，便于按学科方向过滤结果。
            source="arxiv",  #标记数据来源，与类属性里的源名保持完全一致。
            source_id=arxiv_id,  #记录源站编号，将来可以拿它回头精确取这篇文献。
            is_oa=True,  #标记为开放获取；注释说明 arXiv 上文献全部是 OA，不必再逐个探测。
            license=None,  #许可证先留空，Atom 里虽有相关节点，但这里不做解析。
            extra={  #开始写额外字段字典，把原始信息原样保留下来备查。
                "arxiv_id":arxiv_id,  #额外字段里再存一份编号，下载方法靠它判断这份文献是否来自 arXiv。
                "primary_category":primary_category,  #存主分类代码，检索结果里可以显示论文属于哪个方向。
                "categories":categories,  #存全部分类代码列表，与关键词内容相同但保留原始形态。
                "journal_ref":journal_ref,  #存原始 journal_ref 字符串，刊名解析失手时还能人工核对。
                "comment":comment,  #存作者留言，里面常含会议名、页数与代码仓库地址。
                "pdf_url":pdf_url,  #存 PDF 直链，下载全文时直接取用，不必重新发请求。
                "abs_url":abs_url,  #存摘要页地址，与顶层链接字段一致，方便比对排查问题。
                "doi":doi,  #存 DOI 副本，跨源合并去重时用得着。
            },  #字典结束并补上逗号，这里最容易漏逗号导致语法错误。
        )  #构造函数调用结束，文档对象已经生成好了。
        doc.license=None  #再次显式把许可证设为 None，强调不轻信源站声明，宁可留空。
        return doc

    @classmethod  #类方法装饰器，因为解析函数要通过类去调用另一个同类方法。
    def parse_feed(cls,xml_text:str)->List[Document]:
        """解析 Atom feed，返回 Document 列表。"""
        if not xml_text:  #先挡掉空输入，请求失败时上层可能传进来空字符串或 None。
            return[]
        try:  #开始异常保护：源站偶尔返回残缺 XML，解析失败不能让整批任务崩掉。
            root=ET.fromstring(xml_text)  #把字符串解析成 XML 根节点，这一步最可能抛出解析异常。
        except ET.ParseError:  #只捕获 XML 解析错误这一种异常，其他异常仍会照常向上抛出。
            return[]  #解析失败就当成没有结果返回空列表，整批任务不会因此中断。
        documents:List[Document]=[]
        for entry in root.findall(_ATOM+"entry"):  #找出根节点下所有 entry 节点，每个 entry 就代表一篇论文。
            doc=cls._to_document(entry)
            if doc is not None:  #只收集转换成功的条目，返回 None 的无效条目直接丢弃。
                documents.append(doc)
        return documents

    @staticmethod  #静态方法装饰器，构查询串不需要访问实例状态。
    def _build_query(query:str,field:str="all")->str:
        """把用户查询包装成 arXiv 的 ``search_query`` 语法。

        多词查询会用引号包成一个短语，避免被拆成 OR 之后召回大量无关结果。
        """
        cleaned=query.replace('"'," ").strip()
        if not cleaned:
            return "all:"
        if any(token in cleaned for token in("AND","OR","ANDNOT",":")):  #检测用户是否已写布尔运算符或字段前缀，写了就认为他在用高级语法。
            return cleaned  #原样返回用户输入；注释说明已写高级语法就不要再包装。
        if " " in cleaned:
            return '%s:"%s"'%(field,cleaned)  #拼成字段加冒号再加带双引号短语的形式，迫使引擎按整体短语匹配。
        return "%s:%s"%(field,cleaned)  #单词查询直接拼成字段加冒号再加词，走最简单的检索形式。

    #注释分隔线，下面进入对外接口实现区。
    #注释小标题：接口实现，也就是基类要求子类提供的那几个方法。
    #收尾的分隔线，与上面两条组成一个小标题框。
    def search(self,query:str,*,limit:int=20,offset:int=0,  #定义检索方法；星号后面是仅关键字参数，调用时必须写参数名，避免传错。
               field:str="all",sort_by:str="relevance",  #继续声明可选参数：检索字段默认全字段，排序默认按相关度。
               **filters:Any)->List[Document]:  #用双星号接收其他过滤条件；arXiv 用不到，但基类接口要求兼容。
        """检索 arXiv。

        ``field`` 可选 ``all`` / ``ti``（标题）/ ``abs``（摘要）/ ``au``（作者）/
        ``cat``（分类）。``sort_by`` 可选 ``relevance`` / ``lastUpdatedDate`` /
        ``submittedDate``。
        """
        max_results=max(1,min(int(limit),300))
        params:Dict[str,Any]={  #开始拼查询参数字典，键名必须与 arXiv 接口约定完全一致。
            "search_query":self._build_query(query,field),  #查询串交给包装函数处理，得到合法的查询参数。
            "start":max(0,int(offset)),  #起始位置不允许为负，用取值较大者兜住，负数会让接口报错。
            "max_results":max_results,  #写入前面夹好的条数上限。
            "sortBy":sort_by,  #写入排序字段，注意这里的键名是驼峰式的写法。
            "sortOrder":"descending",  #固定按降序返回，也就是最新的或最相关的排在前面。
        }  #参数字典结束，下面把请求发给 arXiv。
        xml_text=self.client.get_text(self.base_url+"/query",params=params)  #用客户端带限流地发 GET 请求并取回 XML 文本；限流由基类统一处理。
        return self.parse_feed(xml_text or "")  #把结果交给解析函数；用空字符串兜底，防止请求失败时传 None 报错。

    def by_id(self,source_id:str)->Optional[Document]:  #定义按编号取单篇的方法，这是取全文下载入口的常用路径。
        """按 arXiv ID 取单篇（如 ``2101.00001``）。"""
        key=self._short_id(source_id) or source_id  #先把编号标准化去掉版本号；若压缩失败就退回原串，保证参数不为空。
        params={"id_list":key,"max_results":1}  #用编号列表参数查询并只要一条，这是 arXiv 提供的按编号取用方式。
        xml_text=self.client.get_text(self.base_url+"/query",params=params)  #同样是带限流的 GET 请求，走的还是 query 这个端点。
        docs=self.parse_feed(xml_text or "")  #解析返回的 feed；注意查不到时 feed 结构依然合法，只是没有条目。
        return docs[0] if docs else None  #有结果就取第一条，没有就返回 None，绝不用下标硬取导致越界。

    def pdf_url(self,doc:Document)->Optional[str]:  #定义下载地址方法，给出这篇文献的全文 PDF 地址供下载器调用。
        """返回 arXiv 全文 PDF 直链。

        只有**确实来自 arXiv** 的文献才返回——判据是存在 ``arxiv_id``。
        否则会去拿别的源塞在 ``extra["pdf_url"]`` 里的地址（可能是出版社
        落地页），然后以 arXiv 客户端的 3 秒限流去下载，既慢又拿到 HTML。
        """
        extra=doc.extra or{}  #取出额外字段字典，为空时兜底成空字典，避免后面取键报错。
        arxiv_id=extra.get("arxiv_id")  #取编号，能取到才说明这篇文献确实来自 arXiv。
        if arxiv_id:  #有编号就按编号拼出标准下载地址并返回，这是最可靠的一条路。
            return "https://arxiv.org/pdf/%s"%arxiv_id  #返回拼好的下载地址，注意新版编号带点，拼出来依然有效。
        if doc.source=="arxiv":  #没有编号但来源标着 arXiv，说明是从 feed 解析来的，可退回备用地址。
            return extra.get("pdf_url")  #返回解析阶段存下的 PDF 地址，可能为空，取不到就是 None。
        return None  #来源不是 arXiv 一律返回 None，避免拿错地址去下载。


__all__=["ArxivSource"]  #声明模块对外只导出这一个类，用星号导入时不会带出内部工具函数。
