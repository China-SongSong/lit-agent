"""通过 Semantic Scholar 查询题录、引用关系和 tldr。"""
from __future__ import annotations

import re
from typing import Any,Dict,List,Optional

from litagent.fetch.base import Source,register_source
from litagent.schema import Document,normalize_doi,parse_authors  #从 schema 导入统一文档模型和 DOI 归一化、作者解析两个工具函数

#注释：说明下面是要向接口索要的字段；字段越多响应越慢，只取用得上的
_FIELDS=",".join([
    "paperId","externalIds","title","abstract","year","venue",  #字段第一部分：论文 ID、外部标识、标题、摘要、年份、期刊名
    "publicationVenue","publicationTypes","publicationDate","journal",  #字段第二部分：出版方信息、出版类型、出版日期、期刊卷页信息
    "authors","citationCount","referenceCount","influentialCitationCount",  #字段第三部分：作者、被引数、参考文献数、有影响力的被引数
    "openAccessPdf","fieldsOfStudy","tldr","isOpenAccess","url",  #字段最后一批：开放获取 PDF、学科领域、tldr 摘要、是否开放、链接
])  #列表写完，用 join 把它们连成接口要的那一长串字段

#注释：说明下面这张表把接口的出版类型翻译成本项目自己的类型
_TYPE_MAP={  #开始定义映射字典，左边是接口给的值，右边是本项目 venue_type 取值
    "JournalArticle":"journal",  #映射：JournalArticle 期刊论文算 journal 期刊
    "Review":"journal",  #映射：Review 综述文章也归到 journal 期刊
    "Conference":"conference",  #映射：Conference 会议论文算 conference 会议
    "Book":"book",  #映射：Book 整本书算 book 图书
    "BookSection":"chapter",  #映射：BookSection 书里的一章算 chapter 章节
    "CaseReport":"journal",  #映射：CaseReport 病例报告归为 journal 期刊
    "ClinicalTrial":"journal",  #映射：ClinicalTrial 临床试验归为 journal 期刊
    "Dataset":"dataset",  #映射：Dataset 数据集算 dataset 数据集
    "Editorial":"journal",  #映射：Editorial 社论归为 journal 期刊
    "LettersAndComments":"journal",  #映射：LettersAndComments 来信评论归为 journal 期刊
    "MetaAnalysis":"journal",  #映射：MetaAnalysis 荟萃分析归为 journal 期刊
    "News":"newspaper",  #映射：News 新闻算 newspaper 报纸
    "Study":"journal",  #映射：Study 研究归为 journal 期刊
    "Patent":"patent",  #映射：Patent 专利算 patent 专利
}  #字典结束，接口的十四种类型都被翻译成本项目认识的类型

_WS_RE=re.compile(r"\s+")  #预先编译好正则：匹配一个或多个连续空白字符，后面多处复用


@register_source  #装饰器：把下面这个类自动登记进数据源注册表，主程序按名字就能找到它
class SemanticScholarSource(Source):
    """Semantic Scholar Graph API 客户端。"""

    name="semanticscholar"  #数据源名字，命令行和配置文件里用 semanticscholar 指代它
    default_rate=1.0  #默认限流速率每秒一次，正对应无 Key 时官方给的每秒一次配额
    supports_search=True  #声明本数据源支持关键词检索
    supports_doi=True  #声明本数据源支持用 DOI 直接查文献
    base_url="https://api.semanticscholar.org/graph/v1"  #接口根地址，后面所有请求都拼在这个前缀后面

    def rate(self)->float:
        return self.settings.s2_rps  #实际速率读配置里的 s2_rps，申请到 Key 之后可以调高

    def headers(self)->Dict[str,str]:
        """配置了 Key 就带上，限流配额会显著放宽。"""
        if self.settings.s2_api_key:  #判断配置里有没有填 s2_api_key
            return{"x-api-key":self.settings.s2_api_key}  #有 Key 就返回带 x-api-key 的请求头，接口凭它给你更高配额
        return{}  #没配 Key 就返回空字典，退回每秒一次的严格限流


    @classmethod  #声明为类方法，方法里用 cls 调用同类中的其它方法
    def _venue_type(cls,paper:Dict[str,Any])->str:
        types=paper.get("publicationTypes") or[]  #取出版类型列表；为空时用 or 兜底成空列表，免得遍历 None 报错
        for item in types:
            mapped=_TYPE_MAP.get(item)  #拿接口类型去查映射表，查不到得到 None
            if mapped:  #只要有映射结果就算命中
                return mapped  #立刻返回，先出现的类型优先，后面的不再看
        pub_venue=paper.get("publicationVenue") or{}  #取出版方信息字典，同样用 or 兜底成空字典
        if(pub_venue.get("type") or "").lower()=="conference":  #出版方类型转小写后等于 conference 就当作会议
            return "conference"
        if paper.get("venue"):
            return "journal"  #就按期刊处理，返回 journal
        return "unknown"  #三条线索都没有，只能返回 unknown 未知

    @classmethod  #同样声明为类方法
    def _to_document(cls,paper:Dict[str,Any])->Optional[Document]:
        title=_WS_RE.sub(" ",(paper.get("title") or "")).strip()
        if not title:
            return None  #直接返回 None，让上层跳过这条没用的记录

        external=paper.get("externalIds") or{}  #取外部标识字典，空时用 or 兜底成空字典
        doi=normalize_doi(external.get("DOI"))  #取 DOI 交给 normalize_doi 归一化，统一成小写并去掉网址前缀
        arxiv_id=external.get("ArXiv")  #取 arXiv 编号，先原样留着，后面放进附加信息
        pubmed_id=external.get("PubMed")  #取 PubMed 编号，同样原样保留
        paper_id=paper.get("paperId")  #取接口自己的 paperId，这是它给出的那串四十位十六进制标识

        authors=parse_authors([
            a.get("name") for a in(paper.get("authors") or[]) if a.get("name")  #列表推导逐条取作者名，没有名字的条目直接过滤，防止 None 崩掉
        ])  #推导结束，结果赋给 authors

        journal=paper.get("journal") or{}  #取期刊详情字典，空时兜底成空字典
        publication_venue=paper.get("publicationVenue") or{}  #取出版方字典，同样兜底
        venue=(  #开始算统一期刊名：按优先级依次尝试三个来源
            publication_venue.get("name")  #优先级最高的是出版方全名，它通常最正式也最完整
            or paper.get("venue")  #没有就退一步用接口的 venue 简写字段
            or journal.get("name")  #再没有就用期刊详情里的名字
        )  #三个候选来源的表达式写完了
        venue=_WS_RE.sub(" ",venue).strip() if venue else None  #有值就再压空白去首尾空白，三个都没有就置 None

        pages:Optional[str]=None
        if journal.get("pages"):  #接口给了页码字段才处理
            pages=str(journal["pages"]).strip() or None  #转字符串去空白；若只剩空串就置 None，不让空值混进结果
        volume=str(journal.get("volume")).strip() if journal.get("volume") else None  #卷号同理：有值就转字符串去空白，没有就保持 None

        oa_pdf=paper.get("openAccessPdf") or{}  #取开放获取 PDF 信息，兜底成空字典
        pdf_url=oa_pdf.get("url") if isinstance(oa_pdf,dict) else None  #只有它确实是字典才取 url，否则置 None，防止接口给字符串时报错

        tldr=paper.get("tldr") or{}  #取 tldr 机器摘要对象，兜底成空字典
        tldr_text=tldr.get("text") if isinstance(tldr,dict) else None  #同样先判断是字典再取 text，得到接口生成的一句话摘要

        return Document(  #开始构造文档对象，把上面提取的字段逐个填进去
            id=Document.make_id("semanticscholar",paper_id,doi,title),  #统一标识：把来源名、论文 ID、DOI、标题依次当候选交给 make_id 归一化
            doi=doi,  #填 DOI 字段
            title=title,  #填前面清洗好的标题，保证没有多余空白
            authors=authors,  #填解析后的作者列表
            year=paper.get("year"),  #填发表年份，接口没给就是 None
            venue=venue,  #填前面按优先级算出来的期刊名
            venue_type=cls._venue_type(paper),  #填出版类型，这里现场调用本类的 _venue_type 判断
            volume=volume,  #填前面提取到的卷号，没有就是 None
            issue=None,  #期号接口没给，直接写 None 占位
            pages=pages,  #填前面提取到的页码，没有就是 None
            url=paper.get("url") or(("https://doi.org/"+doi) if doi else None),  #优先用接口给的链接；没有就拼 DOI 网址，再没有留 None
            language="zh" if any("\u4e00"<=ch<="\u9fff" for ch in title) else "en",  #用汉字码位区间判断标题里有没有中文，有就标 zh 否则标 en
            abstract=(_WS_RE.sub(" ",paper.get("abstract") or "").strip() or None),  #摘要先压空白去首尾；结果变成空串就填 None，保持数据干净
            keywords=[f for f in(paper.get("fieldsOfStudy") or[]) if f],
            source="semanticscholar",  #标明数据来源是 semanticscholar
            source_id=paper_id,  #记录数据源自己的论文 ID，将来可以凭它再取一次详情
            is_oa=bool(paper.get("isOpenAccess")) or bool(pdf_url),  #接口说开放获取或者有 PDF 链接，就判定为开放获取
            citation_count=paper.get("citationCount"),  #填被引次数，这是判断论文影响力的直接指标
            extra={  #开始填附加字典，存放本项目主字段装不下的信息
                "s2_paper_id":paper_id,  #附加里存接口的 paperId
                "arxiv_id":arxiv_id,  #附加里存 arXiv 编号
                "pubmed_id":pubmed_id,  #附加里存 PubMed 编号
                "pdf_url":pdf_url,  #附加里存开放获取 PDF 直链，下载全文时用得上
                "tldr":tldr_text,  #附加里存 tldr 一句话摘要，这是本数据源的招牌能力
                "reference_count":paper.get("referenceCount"),  #附加里存参考文献数量
                "influential_citation_count":paper.get("influentialCitationCount"),  #附加里存有影响力的被引数，比普通被引更能说明分量
                "publication_types":paper.get("publicationTypes") or[],  #附加里存出版类型原始列表，保留原样备用
                "external_ids":external,  #附加里存全部外部标识，DOI、ArXiv、PubMed、CorpusId 都在里面
            },  #附加字典结束，十项额外信息一起交给文档对象
        )  #文档对象构造结束，作为本方法的返回值

    #注释分隔线，下面开始讲接口实现
    #注释小标题：接口实现
    #注释分隔线，接口实现部分正式开始
    def search(self,query:str,*,limit:int=20,offset:int=0,  #定义 search；星号后面的参数必须写名字传，避免位置传参传错
               year_from:Optional[int]=None,year_to:Optional[int]=None,  #继续声明年份下限和上限，默认都不限制
               venue_type:Optional[str]=None,open_access_only:bool=False,  #继续声明出版类型筛选和只看开放获取的开关
               **filters:Any)->List[Document]:
        """关键词检索。``offset`` 最大 9999。"""
        size=max(1,min(int(limit),100))
        params:Dict[str,Any]={  #开始搭请求参数字典，标注类型方便阅读
            "query":query,  #查询词原样放进参数
            "limit":size,  #每页条数用夹紧后的 size
            "offset":max(0,min(int(offset),9999)),  #起始位置夹在 0 到 9999，超范围接口会直接报错
            "fields":_FIELDS,  #带上前面拼好的字段串，只要这些字段
        }  #参数字典结束，五个参数已经准备齐全
        year_filter=self._year_filter(year_from,year_to)
        if year_filter:  #有年份条件才加这个参数
            params["year"]=year_filter  #年份区间放进参数 year
        if open_access_only:
            params["openAccessPdf"]=""  #给 openAccessPdf 传空字符串，接口约定出现这个键就表示筛选
        if venue_type=="conference":
            params["publicationTypes"]="Conference"  #用出版类型参数限定为 Conference 会议
        elif venue_type=="journal":  #否则如果要的是期刊论文
            params["publicationTypes"]="JournalArticle,Review"  #限定为 JournalArticle 和 Review 两类，综述也算期刊文章

        payload=self.client.get_json(  #调用 HTTP 客户端的 get_json 发请求拿 JSON 结果
            self.base_url+"/paper/search",params=params,headers=self.headers()  #地址是根地址加检索路径，带上参数和请求头（头部里可能带 Key）
        )
        if not payload:  #如果返回是空，说明请求失败或者没有结果
            return[]  #返回空列表，让上层当没结果处理，不抛异常打断整条流程
        return self.parse_results(payload.get("data") or[])  #否则从 data 取论文列表，兜底成空列表后交给解析函数

    @staticmethod  #声明为静态方法，它既不需要 self 也不需要 cls
    def _year_filter(year_from:Optional[int],year_to:Optional[int])->Optional[str]:
        """Semantic Scholar 用 ``2015-2020`` / ``2015-`` / ``-2020`` 表示年份区间。"""
        if year_from and year_to:  #起止年份都给了，就走闭区间这一支
            return "%d-%d"%(int(year_from),int(year_to))  #用百分号格式化把两个整数拼成起止格式
        if year_from:  #只给了起始年份，表示从这个年份往后查
            return "%d-"%int(year_from)  #拼成起始年份加短横线，表示这一年之后
        if year_to:  #只给了结束年份，表示查到这个年份为止
            return "-%d"%int(year_to)  #拼成短横线加结束年份，表示这一年之前
        return None  #一个都没有就返回 None，调用方就不会加年份参数

    def parse_results(self,items:List[Dict[str,Any]])->List[Document]:
        documents:List[Document]=[]
        for paper in items:
            doc=self._to_document(paper)
            if doc is not None:  #只有转换成功也就是不是 None 才收下
                documents.append(doc)
        return documents

    def _get_paper(self,paper_ref:str,*,with_references:bool=False
                   )->Optional[Document]:  #续行写返回值类型：要么是文档对象，要么是 None
        """按 ``DOI:`` / ``ARXIV:`` / ``PMID:`` / 裸 paperId 取一篇。"""
        fields=_FIELDS  #字段串先取默认那份
        if with_references:
            fields+=",references.paperId,references.title,references.externalIds"  #在字段串后追加参考文献的三个子字段，接口用点号表示取嵌套字段
        params={"fields":fields}
        payload=self.client.get_json(  #发请求取单篇详情
            "%s/paper/%s"%(self.base_url,paper_ref),  #地址是根地址加论文路径再加标识，标识直接当路径段用
            params=params,headers=self.headers(),allow_404=True,  #带参数与请求头，并允许 404：论文不存在是正常情况，不该抛异常
        )  #这次请求调用结束，结果已经拿到
        if not payload or not isinstance(payload,dict) or not payload.get("title"):
            return None
        doc=self._to_document(payload)
        if doc is not None and with_references:  #转换成功而且这次要参考文献时才补引用关系
            refs=[]
            for ref in((payload.get("references") or[])):  #遍历接口返回的参考文献条目，外层多包一层括号只为排版
                ref_doi=normalize_doi((ref.get("externalIds") or{}).get("DOI"))  #取该文献的 DOI 并归一化
                if ref_doi:  #有 DOI 就用 DOI 做统一标识
                    refs.append("doi:"+ref_doi)  #拼成 doi 冒号加 DOI，这是本项目统一引用标识的第一种格式
                elif ref.get("paperId"):  #没有 DOI 但有 paperId
                    refs.append("s2:"+str(ref["paperId"]))  #退回用 s2 冒号加 paperId 的格式，转成字符串防止类型出问题
            doc.references=refs
        return doc

    def by_doi(self,doi:str)->Optional[Document]:  #定义 by_doi，按 DOI 取单篇
        norm=normalize_doi(doi)  #先归一化传进来的 DOI
        if not norm:  #归一化后为空说明 DOI 不合法
            return None
        return self._get_paper("DOI:"+norm)  #用 DOI 冒号前缀拼成接口认识的形式再取详情

    def by_id(self,source_id:str)->Optional[Document]:
        key=source_id.strip()  #去首尾空白，防止用户粘贴时带上空格
        if not key:  #去空白后是空字符串，说明没给有效标识
            return None
        #注释：裸 paperId 可以当路径段，带 DOI、ARXIV、PMID 前缀的写法也支持
        return self._get_paper(key)  #直接交给 _get_paper，前缀和裸标识都由它转给接口

    def get_references(self,paper_id:str,*,limit:int=100)->List[Document]:
        """取一篇论文的参考文献（用于顺着引用往回追溯）。"""
        params={"fields":_FIELDS,"limit":max(1,min(int(limit),1000))}  #参数带字段串，条数夹在 1 到 1000，接口上限就是一千
        payload=self.client.get_json(
            "%s/paper/%s/references"%(self.base_url,paper_id),  #地址是在论文地址后加参考文献子路径
            params=params,headers=self.headers(),allow_404=True,  #带参数、请求头并允许 404
        )  #这次请求调用结束，返回的 JSON 存在 payload 里
        if not payload:
            return[]
        items=[entry.get("citedPaper") for entry in(payload.get("data") or[])]  #接口把每篇引文包在 citedPaper 里，这里逐条剥出来
        return self.parse_results([i for i in items if i])  #过滤掉空条目后统一解析成文档列表返回

    def get_citations(self,paper_id:str,*,limit:int=100)->List[Document]:
        """取引用了一篇论文的后续工作（用于往前追踪最新进展）。"""
        params={"fields":_FIELDS,"limit":max(1,min(int(limit),1000))}  #参数写法与参考文献接口一致，条数同样夹在 1 到 1000
        payload=self.client.get_json(
            "%s/paper/%s/citations"%(self.base_url,paper_id),  #地址换成引用关系子路径
            params=params,headers=self.headers(),allow_404=True,  #带参数、请求头并允许 404
        )  #这次调用结束，引用关系数据已经拿到
        if not payload:
            return[]
        items=[entry.get("citingPaper") for entry in(payload.get("data") or[])]
        return self.parse_results([i for i in items if i])  #同样过滤空值后解析成文档列表返回

    def pdf_url(self,doc:Document)->Optional[str]:  #定义 pdf_url 方法，供下载模块询问这篇论文的全文直链
        return(doc.extra or{}).get("pdf_url")  #从附加信息里取 pdf_url，没有附加字典时兜底成空字典再取，不会崩


__all__=["SemanticScholarSource"]  #模块导出清单：只对外暴露数据源类这一个名字
