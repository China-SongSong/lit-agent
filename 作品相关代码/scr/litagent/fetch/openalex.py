"""查询 OpenAlex 文献元数据和开放获取地址。"""
from __future__ import annotations  #'from __future__ import annotations'必须放在文件最前面，让类型标注延迟求值，循环导入时才不会炸

import html  #'html.unescape'专治来源名里的'&amp;'，不反转义会原样印进引用条目
import re  #'re'只用来压连续空白，见'invert_abstract'里的说明
from typing import Any,Dict,Iterable,List,Optional  #'Dict'/'List'这些老式写法是为了兼容不认'list[str]'的旧解释器

from litagent.fetch.base import Source,register_source  #'Source'是抽象基类，'register_source'是登记装饰器，两样都得从基类取
from litagent.schema import Document,normalize_doi,parse_authors  #'normalize_doi'负责把DOI各种写法归一化，'parse_authors'负责拆作者串

#OpenAlex 类型词表
#: OpenAlex 的 work.type -> 本项目的 venue_type
#精确匹配优先，命中就直接返回，不走下面的词根猜测
#词表在持续扩充，只靠这张表会在新词出现时静默落到'unknown'
#会议论文的类名换过：早年叫'proceedings-article'，后来多了'conference-paper'
#静默落到'unknown'的后果很隐蔽：下方格式化器会把它当电子资源输出成'[EB/OL]'
#'unknown'是venue_type的默认值，一旦落进去引用格式就整体走电子资源分支
#所以'_map_work_type'在字典之外还有一层词根兜底，专门接新词
_TYPE_MAP={  #键是OpenAlex的work.type原样小写，值是本项目的venue_type
    "article":"journal",  #原创论文和综述都当期刊论文处理，引用格式上没有区别
    "review":"journal",
    "book":"book",  #整本书单独立目，和章节的著录规则不一样
    "book-chapter":"chapter",  #书章节要带'//'和主编信息，所以必须和书分开
    "proceedings-article":"conference",  #会议论文的四种写法全部归到'conference'
    "conference-paper":"conference",
    "conference-abstract":"conference",
    "conference-proceeding":"conference",
    "dissertation":"thesis",  #学位论文走'[D]'，和期刊论文的'[J]'不同
    "report":"report",
    "standard":"standard",  #标准走'[S]'
    "patent":"patent",  #专利走'[P]'
    "dataset":"dataset",  #数据集单列一类，引用格式接近电子资源但有独立标识
    "preprint":"preprint",  #预印本和已发表版本内容可能不同，必须区分开
    "posted-content":"preprint",  #'posted-content'是OpenAlex对预印本平台的另一套叫法
    "editorial":"journal",  #社论、读者来信、勘误都挂在期刊上，按期刊论文著录即可
    "letter":"journal",
    "erratum":"journal",
    "paratext":"chapter",  #'paratext'指正文之外的辅文，实质是章节级内容
    "reference-entry":"chapter",  #'reference-entry'是工具书词条，同样按章节处理
    "peer-review":"journal",  #同行评审报告挂在期刊下，跟着期刊走
    "other":"unknown",  #认不出来的一律给'unknown'，交给词根兜底再判一次
}

#来源类型兜底表
#: OpenAlex 的 source.type 兜底映射（当 work.type 缺失时用）
_SOURCE_TYPE_MAP={
    "journal":"journal",  #键是OpenAlex的source.type，值是本项目的venue_type
    "conference":"conference",  #期刊来源映射到期刊
    "book series":"book",  #会议来源映射到会议
    "ebook platform":"book",  #丛书按书处理
    "repository":"unknown",  #电子书平台按书处理
    "metadata":"unknown",  #仓储库和元数据站给不出稳定的类型，只能算未知
}


#类型映射
def _map_work_type(work_type:str,source_type:str="")->str:
    """把 OpenAlex 的 type 映射成本项目的 venue_type。

    先查精确字典，再用词根匹配兜底——后者保证 OpenAlex 新增类型词时不会退化成
    'unknown'。顺序上先查字典后查词根，是因为字典里有人工确认过的口径
    （比如 'posted-content' 归 preprint），词根匹配只能靠字符串包含关系去猜。
    """
    wt=(work_type or "").lower().strip()  #统一转小写并去空白，OpenAlex的类型词大小写和空格都不稳定
    st=(source_type or "").lower().strip()  #来源类型同样先归一化，后面查兜底表要用

    if wt in _TYPE_MAP and _TYPE_MAP[wt]!="unknown":  #字典命中且不是'unknown'才算数，落'unknown'时继续往下猜词根
        return _TYPE_MAP[wt]

    # 词根兜底（顺序有讲究："book-chapter" 要在 "book" 之前判断）
    if "chapter" in wt:  #'chapter'必须排在'book'之前判断：'book-chapter'同时含这两个子串
        return "chapter"
    if "conference" in wt or "proceedings" in wt:  #'conference'和'proceedings'都指向会议
        return "conference"
    if "journal" in wt:  #'journal'是期刊的另一个词根
        return "journal"
    if "book" in wt:  #'book'放最后，前面已经截走了'book-chapter'
        return "book"
    if "preprint" in wt or "posted" in wt:  #'posted'是预印本平台的词根
        return "preprint"
    if "dataset" in wt:
        return "dataset"
    if "report" in wt:
        return "report"
    if "dissertation" in wt or "thesis" in wt:  #'dissertation'和'thesis'是学位论文的两套叫法
        return "thesis"
    if "standard" in wt:
        return "standard"
    if "patent" in wt:
        return "patent"

    #work.type实在认不出来，退回看来源类型
    return _SOURCE_TYPE_MAP.get(st,"unknown")  #来源也认不出来才给'unknown'，这就是最后一道兜底


#文本清洗
def _unescape(text:Optional[str])->Optional[str]:
    """反转义 HTML 实体，取不到内容时返回 None。

    OpenAlex 的来源名里经常带 '&amp;'（例如 "Information &amp; Knowledge
    Management"），不处理会原样进入引用条目，最终在 GB/T 7714 里印出一个
    '&amp;' 给读者看。'html.unescape' 对不含实体的串是幂等的，可以放心直调。
    """
    if not text:  #空串和'None'一律早退成'None'，让调用方用'or'接下一个候选值
        return None
    value=html.unescape(str(text)).strip()  #'html.unescape'后再去首尾空白，实体反转义可能带出多余空格
    return value or None  #去空白后变成空串的也返回'None'，否则引用条目里会出现一个空来源名

#返回字段裁剪
#: 精简返回字段，显著降低传输量与解析开销
_SELECT_FIELDS=",".join([  #'select'参数让OpenAlex只回这些字段：不列的话单条work会带回几十个字段和整张引用关系
    "id","doi","title","display_name","publication_year","publication_date",  #'id'用于本地去重和反查，'doi'用于跨源判重，两个都不能省
    "authorships","primary_location","best_oa_location","open_access",  #'authorships'带作者以及机构信息，是作者字段的唯一来源
    "type","type_crossref","biblio","abstract_inverted_index","language",  #'primary_location'给落地页和来源名，'best_oa_location'给OA全文，两者可能不同
    "cited_by_count","referenced_works","keywords","is_retracted",  #'open_access'给是否OA和OA状态，用来填'Document.is_oa'
])  #'type'是类型映射的输入，'type_crossref'留着做交叉核对
  #'biblio'里有卷期页码，是著录的必需项
  #'abstract_inverted_index'是摘要的唯一来源，OpenAlex不提供现成摘要文本
#倒排摘要还原
def invert_abstract(inverted:Optional[Dict[str,List[int]]])->Optional[str]:  #'language'缺失时要靠标题猜，所以必须带回来
    """把 OpenAlex 的倒排摘要还原成正常文本。

    OpenAlex 为省空间把摘要存成 '{词: [位置号, ...]}'，一个词出现在多处时
    对应多个位置号。还原算法：先按位置号建一张「位次到词」的表，再对位置号排序
    按序取词；遇到位置号跳号就补一个空格——跳掉的那个位置号正是原文标点或空白
    所在，只有用它占位，还原出来的摘要才断得开，否则整段会连成一片。

    返回 None 表示「这篇没有摘要」，调用方据此把 'Document.abstract' 留空。

    附带说明：'abstract_inverted_index' 是 OpenAlex 给出摘要的唯一形式，它不提供
    现成的摘要文本，所以 '_SELECT_FIELDS' 里必须把这个字段列上；同时也正是靠
    它的「位置号 + 词」结构，本文件才能在只传少量字段的前提下拿到完整摘要。
    """
    if not inverted:  #没有倒排表或者表是空的，直接返回'None'表示这篇没摘要
        return None  #倒排表为空是常态：OpenAlex对相当一部分文献没有摘要
    positions:Dict[int,str]={}
    for word,indexes in inverted.items():
        for index in indexes:
            positions[int(index)]=word  #'int'强转是因为位置号偶尔是字符串形式的数字，不转排序会按字典序出错
    if not positions:  #表建完还是空的，说明原始数据里一个词都没有
        return None
    ordered=sorted(positions)
    parts:List[str]=[]  #逐词拼装，最后统一join，避免在循环里反复做字符串拼接
    previous:Optional[int]=None  #'None'当哨兵表示「前面还没有词」，不能用0代替，因为0是合法位次
    for index in ordered:
        if previous is not None and index!=previous+1:  #位置号跳号就说明中间那个位次原文是标点或空白
            parts.append(" ")  #补一个空格占位：不补的话整段摘要会连成一个没有分隔的长串
        parts.append(positions[index])
        previous=index  #记下当前位次，下一轮靠它判断连不连续
    #原来这里是一段三行注释，说明为什么不能只替换一次就完事
    #正则替换一次就能把任意长度的连续空白收成一个，不用循环补救
    #用'str.replace'换'  '为' '只会替换不重叠的那些，三个空格会剩两个
    text=re.sub(r"\s+"," "," ".join(parts)).strip()  #用正则压连续空白：跳号连续出现时会插入多个空格，最后再收成一个
    return text or None  #全是空白就等于没还原出内容，返回'None'而不是空串


#数据源注册
@register_source
class OpenAlexSource(Source):
    """OpenAlex Works API 客户端。

    只负责拼参数、翻页、把响应交给 '_to_document' 做字段映射，自身不做任何
    数据加工：这样换 API 版本时改动面就锁在参数拼装这一层。
    """

    name="openalex"  #数据源短名，同时也是HTTP缓存目录名
    default_rate=8.0  #默认8次每秒，OpenAlex官方对礼貌池给的上限就在这个量级
    supports_search=True  #支持关键词检索，所以runner会把它排进检索源列表
    supports_doi=True  #支持按DOI精确取，补全流水线能直接拿它查重
    base_url="https://api.openalex.org"  #OpenAlex接口根地址，拼路径时统一用这个前缀

    #限流配置
    def rate(self)->float:
        return self.settings.openalex_rps  #从配置读实际值，让用户可以按'LIT_OPENALEX_RPS'调

    #'rate'覆写基类方法，把限流值改成从配置读，默认8次每秒
    #这里返回的是QPS而不是间隔秒数，和arXiv那种按秒配置的源不一样
    #取值时不做兜底：配置项有默认值8.0，读不到才会暴露真实配置问题
    #小工具
    @staticmethod
    def _short_id(raw:Optional[str])->Optional[str]:
        """把 'https://openalex.org/W2963403868' 压成 'W2963403868'。

        短 ID 有两个用处：一是塞进 'Document.source_id' 当本地去重键（URL 形式太长
        且不同写法指同一篇），二是拼 '/works/{id}' 反查单篇时 OpenAlex 只认短 ID
        或完整 URL，混着传容易 404。
        """
        if not raw:
            return None  #空值早退，避免下面的'rsplit'在'None'上崩掉
        return raw.rstrip("/").rsplit("/",1)[-1]  #先去尾部斜杠再按最后一个斜杠切，两种URL写法都能压成短ID

    #类型判定
    @classmethod
    def _venue_type(cls,work:Dict[str,Any])->str:  #work.type和source.type合起来判定venue_type
        source=((work.get("primary_location") or{}).get("source") or{})  #来源信息挂在'primary_location'下，两级都可能缺失，逐级用'or'兜底成空字典
        mapped=_map_work_type(work.get("type") or "",source.get("type") or "")  #work.type和source.type都传进去，缺哪个就用另一个补
        # OpenAlex 把大量会议论文也标成 article，用来源类型纠正
        if mapped=="journal" and(work.get("type") or "").lower()=="article":  #OpenAlex把大量会议论文也标成'article'，靠来源类型纠正回来
            if(source.get("type") or "").lower()=="conference":  #只有映射结果落在'journal'时才需要纠正，本来就是会议的直接放行
                return "conference"  #来源类型是会议就改判成会议论文，否则引用格式会带错标识
        return mapped  #两种纠正都走不到就原样返回映射结果

    #字段映射
    @classmethod
    def _to_document(cls,work:Dict[str,Any])->Optional[Document]:
        """把一条 OpenAlex work 转成 Document。

        标题为空直接返回 None——没有标题的条目既没法引用也没法展示，与其带着空标题
        流到下游再报错，不如在这里丢掉。

        字段映射的几处取舍：
        - 作者优先取 'display_name'（OpenAlex 统一成「名 姓」西文顺序），
          'raw_author_name' 顺序不固定且有时缺中间名，只作兜底
        - 'venue' 走 '_unescape'，因为来源名里的 '&amp;' 会原样进引用条目
        - 'language' 缺失时按标题里有没有中日韩汉字判中英，比默认成 'en' 准
        - 'referenced_works' 截前 200 条：一篇文章可能引上千篇，全存进 'extra'
          会让本地索引文件膨胀好几倍，而扩展网络用前 200 条已经够
        - 'fetched_at' 置 None 交给 runner 统一打时间戳，同一个批次时间戳才一致
        """
        title=(work.get("title") or work.get("display_name") or "").strip()  #标题优先取'title'，为空再退到'display_name'，两者是同一内容的两种叫法
        title=html.unescape(title) if title else ""  #标题同样要反转义，'&amp;'留在标题里会直接出现在参考文献列表
        if not title:  #标题为空就丢弃：没有标题既排不了序也引用不了
            return None

        doi=normalize_doi(work.get("doi"))  #DOI先归一化，同一篇论文的不同DOI写法才能判成同一篇
        source_id=cls._short_id(work.get("id"))  #ID压成短形式，存进'source_id'做本地去重键

        #作者
        # ---- 作者 ----
        # OpenAlex 只提供 display_name（统一为「名 姓」西文顺序）与
        # raw_author_name（出版方原始串，顺序不固定，有时还缺中间名）。
        # display_name 更规范，优先用它；raw 仅作兜底与审计。
        author_names:List[str]=[]
        for authorship in work.get("authorships") or[]:  #没有作者信息时给空列表，循环体自然不执行
            author=authorship.get("author") or{}  #'author'这一级可能缺失，用'or'兜底成空字典免得取属性报错
            name=(author.get("display_name") or authorship.get("raw_author_name") or "").strip()  #优先'display_name'：OpenAlex已统一成「名 姓」西文顺序，raw串顺序不固定
            if name:  #名字去空白后为空就跳过，避免作者列表里混进空条目
                author_names.append(name)  #收集到名字就追加，顺序保持API返回的原始次序
        parsed_authors=parse_authors(author_names)  #交给'schema.parse_authors'处理，作者格式规则集中在那一个函数里

        #出版物
        # ---- 出版物 ----
        primary=work.get("primary_location") or{}  #'primary_location'缺失时给空字典，后面连续取字段才不会炸
        source_info=primary.get("source") or{}  #来源信息再降一级，两级都缺时'venue'和'publisher'自然为空
        best_oa=work.get("best_oa_location") or{}  #'best_oa_location'是OpenAlex挑出的最佳OA位置，比'primary_location'更适合取全文
        open_access=work.get("open_access") or{}  #'open_access'给是否OA和OA状态，是判断能不能下载的依据
        biblio=work.get("biblio") or{}  #'biblio'里是卷期页码，缺了就给空字典

        #页码拼装
        pages:Optional[str]=None
        first,last=biblio.get("first_page"),biblio.get("last_page")  #'first_page'和'last_page'可能缺失，取到'None'时下面自然走不到拼装分支
        if first and last and str(first)!=str(last):  #首尾页码不相同才拼成区间，相同说明只占一页
            pages="%s-%s"%(first,last)  #用'%s'而不是f-string，是为了和老代码的写法保持一致
        elif first:  #只有首页没有尾页时按单页处理
            pages=str(first)  #两种页码都缺就保持'None'，让引用格式化器自己决定省略

        venue=_unescape(source_info.get("display_name")) or _unescape(  #来源名优先取规范化后的'display_name'
            primary.get("raw_source_name"))  #取不到再退到出版方给的原始串'raw_source_name'
        publisher=_unescape(source_info.get("host_organization_name"))  #出版方名称同样要反转义，里面的'&amp;'会原样进引用条目

        #语言推断
        language=(work.get("language") or "").strip() or(  #语言缺失时空串会被下面的'or'接住
            "zh" if any("\u4e00"<=ch<="\u9fff" for ch in title) else "en"  #标题里只要能找到汉字就判中文，比一律默认成英文准得多
        )

        #关键词
        keywords=[  #关键词在OpenAlex里是对象列表，这里只挑出显示名
            k.get("display_name") for k in(work.get("keywords") or[])
            if k.get("display_name")  #显示名为空的条目直接丢弃，避免关键词列表里出现空串
        ]

        doc=Document(
            id=Document.make_id("openalex",source_id,doi,title),  #ID由源名、短ID、DOI、标题四要素合成，保证同一篇论文在任何路径下算出同一个ID
            doi=doi,  #归一化后的DOI写回文档，跨源判重全靠它
            title=title,  #标题用反转义后的版本
            authors=parsed_authors,  #作者是'Author'对象列表，由'parse_authors'负责拆姓名
            year=work.get("publication_year"),  #年份缺失时留'None'，不要用0或当前年份冒充
            venue=venue,  #'venue'是期刊或会议名，缺了就是'None'
            venue_type=cls._venue_type(work),  #类型走'_venue_type'，因为还要结合来源类型纠正会议论文
            volume=biblio.get("volume"),  #卷号直接取'biblio'里的值
            issue=biblio.get("issue"),  #期号同样直接取
            pages=pages,  #页码用上面拼好的结果
            publisher=publisher,  #出版方缺了就是'None'，引用格式化器会省略这一段
            url=primary.get("landing_page_url") or(("https://doi.org/"+doi) if doi else None),  #没有落地页就用DOI拼一个：'https://doi.org/'是DOI的通用解析地址
            language=language,  #上面已经保证过非空，直接写'language'即可
            abstract=invert_abstract(work.get("abstract_inverted_index")),  #摘要走倒排还原，没有摘要时得到'None'
            keywords=keywords,  #关键词列表直接透传
            source="openalex",  #源名固定写'openalex'，和登记的'name'保持一致
            source_id=source_id,  #源内ID存短形式，反查单篇时直接用
            license=best_oa.get("license") or primary.get("license"),  #许可优先取最佳OA位置的，没有再退到主位置
            is_oa=bool(open_access.get("is_oa")),  #'is_oa'必须是布尔值，所以用'bool'包一层，防止拿到'None'
            citation_count=work.get("cited_by_count"),  #被引次数用于排序，缺失时留'None'
            extra={
                "openalex_id":source_id,  #短ID再存一份进'extra'，方便下游直接取用而不必解析'source_id'
                "type":work.get("type"),  #原始类型词保留下来，排查类型映射错误时全靠它
                "type_crossref":work.get("type_crossref"),  #'type_crossref'是Crossref口径的类型，留着做交叉核对
                "oa_status":open_access.get("oa_status"),  #'oa_status'区分金色、绿色、青铜等开放获取形态
                "oa_url":open_access.get("oa_url"),  #'oa_url'是OA全文地址，但经常指向落地页HTML而不是PDF直链
                "pdf_url":best_oa.get("pdf_url") or primary.get("pdf_url"),  #'pdf_url'优先取最佳OA位置的，没有再退到主位置
                "is_retracted":work.get("is_retracted"),  #'is_retracted'是撤稿标记，会影响文献能不能被引用
                "referenced_works":[
                    cls._short_id(r) for r in(work.get("referenced_works") or[])  #引用关系逐条压成短ID，方便本地建引用网络
                ][:200],  #截前200条：一篇论文可能引上千篇，全存进'extra'会让索引文件膨胀好几倍
            },
        )
        doc.fetched_at=None  #置'None'而不是在这里打时间戳，同一个批次的时间戳才一致
        return doc

    #接口实现
    # ------------------------------------------------------------------
    # 接口实现
    # ------------------------------------------------------------------
    def search(self,query:str,*,limit:int=20,offset:int=0,
               year_from:Optional[int]=None,year_to:Optional[int]=None,
               venue_type:Optional[str]=None,open_access_only:bool=False,
               sort:str="relevance_score:desc",
               **filters:Any)->List[Document]:
        """关键词检索。

        OpenAlex 的 'page' 从 1 开始而本项目的 'offset' 从 0 开始，所以要把
        'offset // per_page + 1' 换算一下；'per-page' 上限 200，超了服务端会报错。

        超过 10000 条时 OpenAlex 会拒绝深翻页并报错，那种量级要改用游标翻页，
        见 'search_all'。
        """
        #分页换算
        per_page=max(1,min(int(limit),200))
        page=(int(offset)//per_page)+1  #'page'从1开始而'offset'从0开始，所以要除完加1

        #过滤条件
        filter_parts:List[str]=[]
        if year_from:  #起始年份拼成'YYYY-01-01'，OpenAlex按日期字符串过滤
            filter_parts.append("from_publication_date:%d-01-01"%int(year_from))
        if year_to:  #结束年份拼成'YYYY-12-31'，用12-31才是把整年包含进来
            filter_parts.append("to_publication_date:%d-12-31"%int(year_to))
        if open_access_only:
            filter_parts.append("is_oa:true")  #'is_oa:true'是OpenAlex的开放获取过滤语法
        if venue_type:
            openalex_types=[k for k,v in _TYPE_MAP.items() if v==venue_type]  #反向查类型表，把本项目口径的'journal'展开成OpenAlex认的类型词
            if openalex_types:  #展开结果为空说明这个词表里没有对应类型，宁可不加过滤也不传空值
                filter_parts.append("type:"+"|".join(openalex_types))  #多个类型词用竖线连接，OpenAlex的过滤器语法要求这样写

        #请求参数
        params:Dict[str,Any]={
            "search":query,
            "per-page":per_page,  #关键词检索走'search'参数，注意它不是过滤条件
            "page":page,  #每页条数
            "select":_SELECT_FIELDS,  #页码，从1开始
        }  #'select'裁剪返回字段，是省流量的关键
        if filter_parts:
            params["filter"]=",".join(filter_parts)  #过滤条件用逗号连接，全部拼成一个'filter'参数
        if sort:
            params["sort"]=sort  #排序为空就不传，交给OpenAlex用它自己的默认排序
        params.update(self.polite_params())  #注入礼貌参数，把联系邮箱带给服务端换取稳定配额

        #发请求并解析
        payload=self.client.get_json(self.base_url+"/works",params=params)  #'/works'是OpenAlex的文献接口，base_url在这里拼上
        if not payload:  #响应为空说明请求失败，早退给空列表
            return[]  #响应里没有'results'键时给空列表，避免在'None'上迭代
        return self.parse_results(payload.get("results") or[])  #交给'parse_results'逐条转换

    def search_all(self,query:str,*,max_results:int=500,
                   page_size:int=200,**filters:Any)->List[Document]:
        """用游标翻页取大量结果，突破 10000 条的 'page' 上限。

        适合「把一个主题的相关文献尽可能抓全」的场景。游标从 '*' 起手，
        每轮用响应里的 'next_cursor' 接上；服务端不给游标就说明到底了。

        和 'search' 一样把 'per-page' 夹在 200 以内；最后一页再按
        'max_results - len(results)' 收窄，避免多抓。
        'select' 参数照样带上，游标翻页传回的数据量比单页翻页大得多。
        """
        #游标翻页
        results:List[Document]=[]  #累加容器，最后统一截断到'max_results'
        cursor="*"  #游标翻页从'*'起手，这是OpenAlex约定的起始游标
        page_size=max(1,min(int(page_size),200))  #'per-page'同样夹在1到200之间，游标翻页也受这个上限约束

        #过滤条件
        filter_parts:List[str]=[]
        year_from=filters.get("year_from")  #'year_from'从关键字参数里取，本方法没有把它列成显式形参
        year_to=filters.get("year_to")  #'year_to'同样从关键字参数取
        if year_from:  #起始年份拼成'YYYY-01-01'，与'search'保持一致
            filter_parts.append("from_publication_date:%d-01-01"%int(year_from))
        if year_to:  #结束年份拼成'YYYY-12-31'
            filter_parts.append("to_publication_date:%d-12-31"%int(year_to))
        if filters.get("open_access_only"):  #开了只取OA就加开放获取过滤
            filter_parts.append("is_oa:true")

        #逐页抓取
        while len(results)<max_results and cursor:  #两个退出条件：抓够了，或者服务端不再给游标
            params:Dict[str,Any]={
                "search":query,  #关键词同样走'search'参数
                "per-page":min(page_size,max_results-len(results)),  #最后一页收窄到剩余所需条数，避免多抓一整页
                "cursor":cursor,  #游标从上轮响应里接过来
                "select":_SELECT_FIELDS,  #'select'裁剪字段，游标翻页传回的数据量比单页翻页还大，更不能省
            }
            if filter_parts:  #过滤条件同样用逗号拼成一个参数
                params["filter"]=",".join(filter_parts)  #过滤条件为空就不传这个参数
            params.update(self.polite_params())  #每页都注入礼貌参数，翻页久了更不能掉邮箱

            payload=self.client.get_json(self.base_url+"/works",params=params)  #接口地址和'search'是同一个，区别只在'cursor'与'page'
            if not payload:  #响应为空说明请求失败，跳出循环保住已抓到的结果
                break
            batch=self.parse_results(payload.get("results") or[])
            if not batch:  #本页一条有效数据都没有就停：继续翻下去也是浪费请求
                break
            results.extend(batch)  #追加到累加容器
            cursor=((payload.get("meta") or{}).get("next_cursor")) or None  #'meta.next_cursor'为'None'说明到底了，用'or None'把空串也归一成'None'

        return results[:max_results]

    #结果转换
    def parse_results(self,raw_results:Iterable[Dict[str,Any]])->List[Document]:
        """把原始 results 数组转成 Document 列表，自动跳过无标题条目。

        跳过而不是抛异常：一条脏数据不该让整轮抓取作废，调用方拿到多少算多少。
        """
        documents:List[Document]=[]  #累加容器，保持API返回的原始次序
        for work in raw_results:
            doc=self._to_document(work)  #没有标题的条目会返回'None'，下面据此跳过
            if doc is not None:  #只收转换成功的条目，脏数据静默丢弃但不打断整轮抓取
                documents.append(doc)  #收集完成后返回
        return documents

    #按 DOI 取
    def by_doi(self,doi:str)->Optional[Document]:
        """按 DOI 精确获取，取不到返回 None。

        'allow_404=True' 是必须的：库里存着大量 DOI，其中总有没被 OpenAlex 收录的，
        不拦下来会直接抛 'HttpError' 打断整条补全流水线。
        """
        norm=normalize_doi(doi)
        if not norm:
            return None
        params:Dict[str,Any]={"select":_SELECT_FIELDS}  #只带'select'，单篇查询不需要分页参数
        params.update(self.polite_params())  #同样注入礼貌参数，单篇查询也是请求
        payload=self.client.get_json(
            "%s/works/https://doi.org/%s"%(self.base_url,norm),  #把DOI拼进路径：OpenAlex支持用'https://doi.org/xxx'形式直接查
            params=params,allow_404=True,  #'allow_404=True'让没收录的DOI返回'None'而不是抛异常，避免打断流水线
        )
        if not payload or not isinstance(payload,dict) or "id" not in payload:  #空响应或者响应里没有'id'的都算没取到，两种都早退成'None'
            return None
        return self._to_document(payload)  #响应本身就是一个work对象，直接推进映射函数

    #按 ID 取
    def by_id(self,source_id:str)->Optional[Document]:
        """按 OpenAlex ID 获取，'W123' 和完整 URL 两种写法都收。"""
        key=source_id.strip()  #去首尾空白，用户从URL栏复制过来的ID常带空格
        if key.startswith("http"):  #传进来的是完整URL就压成短ID，OpenAlex只认短ID或完整URL
            key=self._short_id(key) or key  #压缩失败时退回原串，宁可让服务端报错也不要传一个空ID上去
        params:Dict[str,Any]={"select":_SELECT_FIELDS}  #只带'select'，单篇查询不需要分页参数
        params.update(self.polite_params())  #注入礼貌参数
        payload=self.client.get_json(
            "%s/works/%s"%(self.base_url,key),params=params,allow_404=True  #接口路径换成短ID形式
        )  #'allow_404=True'让不存在的ID返回'None'，和'by_doi'保持同一套容错口径
        if not payload or not isinstance(payload,dict) or "id" not in payload:
            return None  #空响应或者响应里没有'id'的都算没取到
        return self._to_document(payload)  #响应本身就是一个work对象，直接推进映射函数

    #PDF 地址
    def pdf_url(self,doc:Document)->Optional[str]:
        """优先取 OA 全文 PDF 地址。

        先挑以 '.pdf' 结尾的那个地址，实在没有才把 'oa_url' 交出去——'oa_url'
        经常指向出版社的落地页 HTML 而不是 PDF 直链，下载后会得到一坨 HTML。
        最后再兜一层 'pdf_url'：哪怕后缀不认，也比返回 None 让下载环节空转强。
        """
        extra=doc.extra or{}  #'extra'可能为空，用'or {}'兜底免得在'None'上取键
        for key in("pdf_url","oa_url"):  #两个候选地址都要看，'pdf_url'优先但可能缺失
            value=extra.get(key)  #取出候选地址
            if value and str(value).lower().endswith(".pdf"):  #以'.pdf'结尾才算真直链，别的不敢当PDF下载
                return str(value)  #命中就转成字符串返回，避免把非字符串对象交给下载器
        oa_url=extra.get("oa_url")  #两个候选都查完了，转去拿'oa_url'
        if oa_url:  #有'oa_url'就用它
            return str(oa_url)  #转成字符串返回，下游要拼请求头
        return extra.get("pdf_url") or None  #实在没有就回'pdf_url'，哪怕后缀不认也比返回'None'让下载环节空转强


__all__=["OpenAlexSource","invert_abstract"]
