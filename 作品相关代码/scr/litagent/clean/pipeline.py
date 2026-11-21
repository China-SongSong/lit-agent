"""把原始文献整理成可索引的正文和片段。

有 PDF 时先抽取正文；已有的 txt、docx、md 全文直接清洗；只有摘要的
文献则用标题和摘要组成一个可检索片段。这一步不建索引，后续调整索引
参数时不需要重新解析 PDF。
"""
from __future__ import annotations  #启用延迟注解：让类型标注以字符串形式保存，从而可以写前向引用而不报错

import time
from dataclasses import dataclass,field
from pathlib import Path  #从 pathlib 导入 Path，用来判断 PDF 路径是不是真实存在的文件
from typing import Callable,List,Optional,Sequence,Tuple

from litagent.clean.chunker import chunk_stats,chunk_text
from litagent.clean.dedupe import DedupeReport,dedupe_documents
from litagent.clean.normalize import(  #开始从规范化模块导入多个清洗函数，左括号表示这次导入要跨多行写
    clean_abstract,
    clean_text,
    detect_language,
    strip_references,
)  #右括号结束这组多行导入，换成逐个导入也能跑，但这样更集中
from litagent.clean.pdf_text import PdfExtraction,extract_pdf  #从 PDF 抽取模块导入抽取结果类型和抽取函数 extract_pdf
from litagent.config import Settings,settings as default_settings  #从配置模块导入设置类型，并把全局 settings 改名成默认设置
from litagent.index.store import Store  #从索引存储模块导入 Store，它是所有数据库读写操作的门面
from litagent.schema import Chunk,Document,content_hash,utcnow


@dataclass  #装饰器 @dataclass，让下面的类自动生成初始化方法，只写字段即可
class CleanReport:
    """清洗任务报告。"""

    processed:int=0  #字段 processed 统计成功处理篇数，默认零，每成功一篇加一
    skipped:int=0  #字段 skipped 统计被跳过的篇数，已清洗过且无需重做的走这里
    failed:int=0  #字段 failed 统计失败篇数，单篇报错只记数不中断整批
    from_pdf:int=0  #字段 from_pdf 统计来源是 PDF 抽取成功的篇数
    from_fulltext:int=0  #字段 from_fulltext 统计来源是已有纯文本全文的篇数
    from_abstract_only:int=0  #字段 from_abstract_only 统计只有摘要、走了兜底逻辑的篇数
    scanned_pdfs:int=0  #字段 scanned_pdfs 统计疑似扫描件篇数，右侧注释点明它需要 OCR
    chunks_created:int=0  #字段 chunks_created 累计这次一共生成了多少个检索片段
    chars_before:int=0  #字段 chars_before 累计清洗前的原始字符数，用来算压缩比
    chars_after:int=0  #字段 chars_after 累计清洗后的字符数，和上一个字段配对使用
    dedupe:Optional[DedupeReport]=None  #字段 dedupe 保存去重报告，默认 None 表示这次没跑去重
    errors:List[str]=field(default_factory=list)  #字段 errors 是错误信息列表，用 factory 给每个实例造一份新列表
    elapsed_seconds:float=0.0  #字段 elapsed_seconds 记录整批清洗花掉的秒数，默认零

    @property  #装饰器 @property，把下面的方法变成可以当属性直接读取的计算值
    def compression(self)->str:
        """清洗前后的字符数变化（去掉页眉页脚/参考文献后的体量变化）。"""
        if not self.chars_before:  #边界判断：清洗前字符数为零就直接返回短横线，避免除零报错
            return "-"
        delta=100.0*(self.chars_after-self.chars_before)/self.chars_before
        return "%+.1f%%"%delta  #用百分号格式化输出，正号表示增长、负号表示缩减，保留一位小数

    def summary(self)->str:
        lines=[  #开始组装输出行列表，第一个元素是标题行
            "清洗报告",  #标题行内容，就是中文的清洗报告四个字
            "  处理成功   : %d 篇（跳过 %d，失败 %d）"  #第二行写处理成功篇数，同行带出跳过与失败的数字
            %(self.processed,self.skipped,self.failed),  #上一行字符串的格式化参数，按顺序填处理、跳过、失败三个计数
            "  来源分布   : PDF 全文 %d / 已有全文 %d / 仅摘要 %d"  #第三行写来源分布，分别列出 PDF 全文、已有全文、仅摘要三类篇数
            %(self.from_pdf,self.from_fulltext,self.from_abstract_only),  #上一行字符串的格式化参数，依次填三个来源计数字段
            "  疑似扫描件 : %d 篇（无文本层，需要 OCR）"%self.scanned_pdfs,  #第四行提示疑似扫描件数量，并说明它们没有文本层需要 OCR
            "  生成片段   : %d 个"%self.chunks_created,  #第五行报告本次新生成的片段总数
            "  字符变化   : %d -> %d (%s)"  #第六行报告字符变化，箭头左边是清洗前右边是清洗后
            %(self.chars_before,self.chars_after,self.compression),  #上一行字符串的格式化参数，第三个位置填入 compression 属性的结果
            "  耗时       : %.1f 秒"%self.elapsed_seconds,  #第七行报告耗时，保留一位小数并带上秒字
        ]  #右方括号结束列表，此时 lines 是一个字符串列表
        if self.dedupe is not None:
            lines.append("")
            lines.append(self.dedupe.summary())
        if self.errors:
            lines.append("  错误样例   :")  #追加一行错误样例的小标题
            for error in self.errors[:8]:  #只取前八条错误，避免错误太多把报告刷得没有重点
                lines.append("    - "+error)
        return "\n".join(lines)  #用换行符把各行拼成一整段文本返回，这里的换行符要写成转义形式


@dataclass  #装饰器 @dataclass，为下面的清洗结果类自动生成初始化方法
class CleanedDocument:
    """单篇文献的清洗结果。"""

    doc:Document  #字段 doc 是被就地更新过的文献记录对象，写库时直接用它
    fulltext:str  #字段 fulltext 是清洗后的完整正文，供写库与后续检索使用
    chunks:List[Chunk]  #字段 chunks 是这篇文献切出的片段列表
    source:str  #字段 source 记录来源类型，注释列出可取 pdf、fulltext、abstract
    extraction:Optional[PdfExtraction]=None  #字段 extraction 保存 PDF 抽取结果，非 PDF 来源时保持默认的空值


class CleanPipeline:
    """清洗流水线。"""

    def __init__(self,store:Store,settings:Optional[Settings]=None)->None:  #构造方法：接收数据库门面对象与可选配置，配置缺省时用全局设置
        self.store=store
        self.settings=settings or default_settings  #把传入配置存成实例属性；为空时退回全局默认设置，省得到处判空

    def _content_level(self,doc:Document)->str:
        """读取或推断内容级别，兼容早期没有写 content_level 的旧数据。"""
        level=(doc.extra or{}).get("content_level")
        if level in ("title","abstract","fulltext"):
            return level
        try:
            sections={chunk.section for chunk in self.store.iter_chunks(doc.id)}
        except Exception:
            sections=set()
        if sections and not sections-{"title","abstract"}:
            return "abstract" if "abstract" in sections else "title"
        return "fulltext" if sections or (doc.fulltext or "").strip() else ""

    #注释分隔线，标记下面这一节是单篇处理相关的代码
    #注释小节标题：单篇处理
    #注释分隔线，和上一行配对把标题夹在中间
    def clean_document(self,doc:Document,*,
                       strip_refs:bool=True)->CleanedDocument:  #第二个参数 strip_refs 默认 True，表示默认要砍掉参考文献段落
        """清洗单篇文献。不抛异常——失败信息写进返回值的 ``doc.extra``。"""
        pdf_path=doc.pdf_path  #取出文献的 PDF 路径存到局部变量，下面要反复判断它
        extraction:Optional[PdfExtraction]=None  #先把抽取结果初始化为 None，表示目前还没有从 PDF 抽取过
        raw_text=""
        source_kind="abstract"

        #注释：只要是真实存在的 .pdf 文件就始终从 PDF 抽取，哪怕库里已有全文
        #注释解释第一条原因：PDF 抽取能给出页码与版面信息，包括章节与双栏还原
        #注释补充：这些版面信息是纯文本全文给不了的，所以不能省掉抽取这一步
        #注释解释第二条原因：第一次清洗会写回 fulltext，这里优先用它会出问题
        #注释：单独一个井号占位，起分隔作用，把前后两段说明隔开
        #注释：本地 txt 与 docx 的全文才是原始内容，它们后缀不是 pdf
        #注释收尾：那类文件不会走下面这个分支，所以这里优先抽 PDF 不会误伤它们
        is_pdf_file=bool(  #开始算布尔变量 is_pdf_file，用左括号把三个条件分行写开
            pdf_path  #第一个条件：pdf_path 本身非空，否则拿它取属性会报错
            and Path(pdf_path).is_file()  #第二个条件：该路径确实存在一个文件，防止文件被删或路径写错
            and Path(pdf_path).suffix.lower()==".pdf"  #第三个条件：把后缀转成小写再比是不是 .pdf，兼容大写后缀
        )  #右括号结束布尔表达式，此时 is_pdf_file 是 True 或 False

        if is_pdf_file:  #如果确实是 PDF 文件，就走抽取分支，这一步优先级最高
            extraction=extract_pdf(pdf_path)  #调用 extract_pdf 抽取正文，把整份结果存进 extraction 备用
            raw_text=extraction.fulltext
            source_kind="pdf"  #把来源类型改写成 pdf，供后面统计来源分布使用
        elif((doc.fulltext or "").strip()
             and self._content_level(doc) not in ("title","abstract")):  #摘要/标题兜底不能再当全文二次清洗
            raw_text=doc.fulltext or ""
            source_kind="fulltext"  #把来源类型改写成 fulltext，表示这份正文不是从 PDF 来的

        if raw_text.strip():
            #注释小标题：逐页清洗，保证页码偏移与清洗后文本严格对齐
            #注释：单独一个井号占位，把标题和下面的原因说明分隔开
            #注释：不能图省事对整篇原始文本清洗一次再分块，那样会出错
            #注释解释原因：清洗会改变文本长度，比如合并断词、去掉页眉
            #注释接着说明：偏移错位的结果是所有片段都被标成第一页
            #注释：单独一个井号占位，把问题描述和正确做法分隔开
            #注释给出正确做法：逐页清洗、逐页累计偏移，每页起始偏移就永远准确
            #注释：截参考文献放在拼接之后做，因为它只砍尾部不影响前面的偏移
            if extraction is not None and extraction.pages:  #判断既抽过 PDF 又拿到了分页正文，两个条件都满足才逐页清洗
                cleaned_pages:List[str]=[]  #新建列表，收集每一页清洗后的文本
                page_offsets:List[Tuple[int,int]]=[]  #新建列表记录每页的起始偏移与页码，每个元素是一个二元组
                cursor=0  #游标变量从零开始，它始终指向下一段文本在拼接结果里的起始位置
                for page in extraction.pages:
                    text=clean_text(page.text,strip_refs=False)  #只清洗这一页的文字，显式关掉截参考文献，因为要留给拼接后统一截
                    page_offsets.append((cursor,page.number))  #先记下当前游标和本页页码；顺序很重要，必须记录完再累加
                    cleaned_pages.append(text)
                    cursor+=len(text)+2  #游标加上本页长度再加二，加二是因为拼接时页间插了两个换行符
                cleaned="\n\n".join(cleaned_pages)  #用两个换行符把各页拼成整篇正文，分隔符长度必须和上一步加的二一致
                if strip_refs:
                    cleaned=strip_references(cleaned).rstrip()  #截掉尾部参考文献并去掉尾随空白，保证清洗后文本结尾干净
            else:  #否则走不分页那条路，说明没有 PDF 分页信息可用
                cleaned=clean_text(raw_text,strip_refs=strip_refs)
                page_offsets=None  # type: ignore[reportAssignmentType]  #偏移表置为 None，告诉分块函数这次没有页码信息可用

            chunks=chunk_text(
                doc.id,cleaned,  #前两个位置参数：文献 id 给片段打归属，清洗后正文是要切的材料
                max_chars=self.settings.max_chunk_chars,  #按名传入最大字符数，取自配置，保证单个片段不会过长
                overlap_chars=self.settings.chunk_overlap_chars,  #按名传入重叠字符数，让相邻片段有一小段重复，避免句子被拦腰切断
                page_offsets=page_offsets,
            )  #右括号结束调用，返回值存进 chunks
            if not chunks:
                #注释说明这种情况要退回摘要级处理，属于兜底分支
                return self._clean_abstract_only(doc,raw_text)  #直接返回只用摘要构造的结果，把原始文本一并交过去兜底

            doc.fulltext=cleaned
            doc.page_count=doc.page_count or(  #页数优先保留原值，原值为空时才用抽取结果补上
                extraction.page_count if extraction else None  #三元表达式取出抽取结果的页数，没有抽取结果时就是 None
            )  #右括号结束赋值；用 or 而不是直接覆盖，避免把已有页数冲掉
            detected=detect_language(cleaned)
            if detected!="unknown":  #只有识别出确定语言才覆盖，避免把 unknown 写进字段
                doc.language=detected
            doc.content_hash=content_hash(doc.id,cleaned[:200000])  #算内容指纹，只取正文前二十万字符，防止超长文献拖慢哈希
            doc.cleaned_at=utcnow()
            doc.extra=dict(doc.extra or{})
            doc.extra["chunk_count"]=len(chunks)  #在 extra 里记下片段个数，这也是往后判断要不要重清洗的标志
            doc.extra["chunk_stats"]=chunk_stats(chunks)  #在 extra 里存片段统计，供报告与排查问题时查看
            if extraction is not None:
                doc.extra["extract_method"]=extraction.method  #记录抽取方法，比如是正常文本层抽取还是降级策略抽取的
                if extraction.errors:
                    doc.extra["extract_errors"]=extraction.errors
                if extraction.removed_lines:
                    #注释：只留前若干条，方便人工核对页眉识别得准不准
                    doc.extra["removed_header_lines"]=extraction.removed_lines[:10]  #只取前十条被删行存进 extra，避免把整篇噪声都存进库里
            doc.extra["content_level"]="fulltext"  #有真正正文的文献，统计和总结都按全文处理
            return CleanedDocument(doc=doc,fulltext=cleaned,chunks=chunks,  #组装并返回单篇清洗结果，传文献对象、正文、片段与来源
                                   source=source_kind,extraction=extraction)  #第二行的参数：来源类型与抽取结果，供调用方统计来源分布

        #注释：没有正文时，就用标题加摘要构造一个可检索的片段
        return self._clean_abstract_only(doc,"")

    def _clean_abstract_only(self,doc:Document,
                             raw_text:str="")->CleanedDocument:  #第二个参数 raw_text 默认空串，正文太短时会把原文也带上
        """只有摘要时的兜底：用「标题 + 摘要」造一个片段。

        这样非 OA 论文至少是**可被检索**的（用户能发现它、能拿到它的引用格式），
        只是无法溯源到具体句子。
        """
        abstract=clean_abstract(doc.abstract) or ""
        pieces=[doc.title.strip()]  #片段材料列表先放标题，先 strip 去掉两头空白
        if abstract:
            pieces.append(abstract)
        if raw_text.strip():
            body="\n\n".join(p for p in pieces if p)+"\n\n"+raw_text.strip()
            min_chars=50  #此时门槛设为五十个字符，因为已经有真正的正文了
            doc.extra=dict(doc.extra or{})
            doc.extra["content_level"]="abstract"
        elif abstract:  #否则如果至少有摘要可用，就走中间这一档
            body="\n\n".join(p for p in pieces if p)  #只用标题加摘要拼接成片段材料
            min_chars=50  #门槛同样是五十，摘要一般够长所以按正常门槛走
            doc.extra=dict(doc.extra or{})
            doc.extra["content_level"]="abstract"
        else:  #否则连摘要都没有，只能走最弱的标题级兜底
            #注释：说明 OpenAlex 对相当一部分文献不提供摘要这个现实情况
            #注释：此时仍然把标题建一个片段，否则这篇文献在库里完全搜不到
            #注释：用户明明把文献抓进来了却查无此文，这种体验很难理解
            #注释：片段标为 title 级，检索层可以据此继续给它降权
            body=doc.title.strip()
            min_chars=8  #门槛降到八个字符，否则标题太短会被分块函数直接丢掉
            doc.extra=dict(doc.extra or{})  #同样把 extra 复制成新字典，避免共享可变对象造成污染
            doc.extra["content_level"]="title"  #在 extra 里标记内容级别为 title，供检索层识别并降权

        if not body.strip():
            doc.extra=dict(doc.extra or{})  #再次确保 extra 是可独立修改的字典
            doc.extra["no_content"]=True  #打上无内容标记，方便事后统计有多少这样的空壳记录
            return CleanedDocument(doc=doc,fulltext="",chunks=[],source="abstract")

        chunks=chunk_text(
            doc.id,body,  #位置参数：文献 id 与片段材料正文
            max_chars=self.settings.max_chunk_chars,  #最大字符数照旧取自配置，保持全库片段长度口径一致
            overlap_chars=0,  #重叠设为零，右边注释解释摘要与标题很短不需要重叠
            min_chars=min_chars,  #最小字符数用上面分支算出的门槛，决定多短的片段才被丢弃
        )  #右括号结束调用，返回值存进 chunks
        for chunk in chunks:  #遍历刚切出的每一个片段
            chunk.section="abstract" if abstract else "title"

        doc.abstract=abstract or doc.abstract
        doc.cleaned_at=utcnow()  #写清洗时间戳，让这篇文献在增量逻辑里被认作已处理
        return CleanedDocument(doc=doc,fulltext=body,chunks=chunks,  #组装并返回兜底结果，正文用拼好的材料，来源固定标摘要
                               source="abstract")  #第二行的参数：来源写死 abstract，因为它确实不是全文清洗来的

    #注释分隔线，标记下面这一节是批量处理与全库操作的代码
    #注释小节标题：批量处理
    #注释分隔线，与上一行配对把标题夹在中间
    def run(self,*,doc_ids:Optional[Sequence[str]]=None,
            limit:Optional[int]=None,  #参数 doc_ids 可指定只处理哪几篇文献，为空表示处理全库
            redo:bool=False,  #参数 limit 限制最多取多少篇，便于先小批量试跑
            with_fulltext_only:bool=False,  #参数 redo 为真时强制重做已清洗过的文献，默认为假
            strip_refs:bool=True,  #参数 with_fulltext_only 为真时跳过只有摘要的文献
            run_dedupe:bool=False,  #参数 strip_refs 默认真，控制是否顺手砍掉参考文献段落
            on_progress:Optional[Callable[[int,int,Document],None]]=None,  #参数 run_dedupe 为真时清洗完对全库跑去重合并
            )->CleanReport:  #参数 on_progress 可传回调函数，每篇处理完调一次报进度
        """批量清洗。

        参数
        ----
        doc_ids
            只处理指定文献；``None`` 表示全库。
        redo
            默认跳过 ``cleaned_at`` 已存在的文献（增量）；``True`` 强制重做。
        with_fulltext_only
            只处理有 PDF 或全文的文献，跳过仅有摘要的。
        run_dedupe
            清洗结束后对全库跑一次去重合并。
        """
        started=time.monotonic()  #用单调时钟记下起始时间，它不受系统时间被人改动的影响
        report=CleanReport()  #新建一份空的清洗报告对象，后面所有数字都往它身上累加

        if doc_ids:
            documents=[self.store.get_document(i) for i in doc_ids]
            documents=[d for d in documents if d is not None]  #把取到的 None 过滤掉，避免后面拿 None 取属性直接崩
        else:  #否则说明要处理全库，换一条取数路径
            documents=list(self.store.iter_documents(with_fulltext=True,limit=limit))  #迭代库里所有文献只取有全文的，并把 limit 直接下推给存储层

        if with_fulltext_only:
            documents=[d for d in documents  #用列表推导过滤一遍，两个条件分两行写开
                         if((d.fulltext or "").strip()
                             and self._content_level(d) not in ("title","abstract"))  #摘要级兜底不能算全文
                         or(d.pdf_path and Path(d.pdf_path).is_file())]  #第二个条件：或者本地 PDF 文件真实存在

        total=len(documents)  #算出本次要处理的文献总数，供进度回调使用
        for index,doc in enumerate(documents,start=1):
            try:  #用 try 包住整篇处理，这是单篇失败不中断整批的关键
                if not redo and(doc.cleaned_at or "").strip() and not self._needs_reclean(doc):  #增量判断三连：没要求重做、已清洗过、且不需要补洗，三条都成立才跳过
                    report.skipped+=1  #命中条件只把跳过计数加一，不碰数据库也不重新解析 PDF
                else:  #否则说明这篇需要真正处理，走下面的正式分支
                    cleaned=self.clean_document(doc,strip_refs=strip_refs)

                    if cleaned.source=="pdf" and cleaned.extraction is not None:
                        before=len(cleaned.extraction.fulltext)  #PDF 用抽取出的原始正文长度
                    elif cleaned.source=="fulltext":
                        before=len(doc.fulltext or "")  #纯文本来源用清洗前原文长度
                    else:
                        before=len(cleaned.fulltext)  #摘要/标题兜底不做正文压缩，前后记为同一体量
                    report.chars_before+=before
                    report.chars_after+=len(cleaned.fulltext)

                    self.store.upsert_document(cleaned.doc)
                    if cleaned.fulltext:  #只有在确实有正文时才写全文，避免用空正文覆盖掉已有内容
                        self.store.set_fulltext(
                            cleaned.doc.id,cleaned.fulltext,
                            mark_fulltext=cleaned.source!="abstract",  #摘要/标题兜底只存文本，不冒充全文
                        )
                    self.store.replace_chunks(cleaned.doc.id,cleaned.chunks)  #用新片段整体替换这篇文献的旧片段，防止重复清洗时片段越积越多

                    report.processed+=1  #成功处理的篇数加一
                    report.chunks_created+=len(cleaned.chunks)
                    if cleaned.source=="pdf":  #如果这份结果来自 PDF 抽取，就归类到 PDF 这一支
                        report.from_pdf+=1  #PDF 来源计数加一
                        if cleaned.extraction is not None:
                            errors=cleaned.extraction.errors or[]  #取出抽取错误列表，为空时兜成空列表再判断
                            if any("扫描件" in e or "文本密度偏低" in e for e in errors):  #只要错误里有扫描件或文本密度偏低字样，就认定是疑似扫描件
                                report.scanned_pdfs+=1  #疑似扫描件计数加一，提醒用户这批文件需要额外 OCR 处理
                    elif cleaned.source=="fulltext":  #否则如果来源是已有的纯文本全文
                        report.from_fulltext+=1  #纯文本来源计数加一
                    else:  #剩下的都归到只有摘要的兜底那一类
                        report.from_abstract_only+=1  #仅摘要来源计数加一
            except Exception as exc:  # noqa: BLE001 单篇失败不中断整批，捕获所有异常，行尾注释写明这样写是为了单篇失败不中断整批
                report.failed+=1  #失败篇数加一；注意这里不重新抛出，循环会继续往下走
                report.errors.append("%s: %s: %s"%(  #开始拼一条错误记录，格式是文献 id 冒号异常类名冒号异常信息
                    doc.id,type(exc).__name__,str(exc)[:150]))  #异常信息截到一百五十个字符，防止超长报错把日志撑爆

            if on_progress:
                try:  #用 try 把回调也包起来，回调出错不该影响正在跑的清洗
                    on_progress(index,total,doc)
                except Exception:  # noqa: BLE001，捕获回调抛出的任何异常
                    pass  #直接忽略，注释表明这里是故意的静默处理

        if run_dedupe:
            report.dedupe=self.dedupe_corpus()

        report.elapsed_seconds=time.monotonic()-started  #用当前时间减去起始时间，算出整批清洗耗时的秒数
        return report

    def _needs_reclean(self,doc:Document)->bool:
        """已清洗过，但内容变了（例如刚补下载了 PDF）→ 需要重做。"""
        if(doc.pdf_path and Path(doc.pdf_path).is_file()  #第一重判断：本地确实存在 PDF 文件
                and not(doc.extra or{}).get("chunk_count")):  #第二重判断：而且 extra 里没有片段数记录，说明当时没抽到正文
            return True  #两个条件都成立就返回真，表示这篇值得重新清洗一遍
        return False  #其余情况返回假，表示已清洗结果仍然可用，跳过即可

    #注释分隔线，标记下面这一节是全库去重相关的代码
    #注释小节标题：全库去重
    #注释分隔线，和上一行配对把标题夹住
    def dedupe_corpus(self)->DedupeReport:
        """对全库跑一次三重去重，把重复文献合并掉。

        合并后主记录吸收其余记录的字段，重复记录连同其片段、索引一并删除。
        """
        documents=list(self.store.iter_documents(
            with_fulltext=True,
            only_cleaned=True,
        ))  #只对 has_fulltext=1 的记录做跨库去重
        unique,report=dedupe_documents(documents)

        for group in report.groups:  #遍历报告里的每一个重复组
            for duplicate in group.duplicates:  #遍历该组里被判为重复的那些文献
                if duplicate.id==group.keeper.id:  #如果这条恰好就是组里要保留的主记录，那就不能删
                    continue  #跳过它，继续处理组里的下一条
                self.store.delete_document(duplicate.id)  #否则调用存储的删除方法，把重复文献连它的片段与索引一起删掉

        #注释：主记录可能吸收了新字段，比如摘要、DOI、卷期页，所以要写回
        for doc in unique:  #遍历去重后剩下的唯一文献
            self.store.upsert_document(doc)

        return report

    #注释分隔线，标记下面这一节是补题录相关的代码
    #注释小节标题：补题录
    #注释分隔线，与上一行配对把标题夹在中间
    def documents_missing_metadata(self,*,limit:Optional[int]=None
                                   )->List[Document]:  #右括号这一行接着写返回类型，表示返回文献对象列表
        """找出题录残缺、值得用在线源补齐的本地文献。

        判据：有 DOI 但缺年份/刊名/卷期页，或作者信息不完整。
        这些通常是从本地 PDF 扫进来的，元数据质量最差。
        """
        result:List[Document]=[]
        for doc in self.store.iter_documents(with_fulltext=False):  #遍历全库中没有全文的文献，这类恰恰最可能缺题录
            if(doc.pdf_path or doc.source=="local") and doc.doi:  #只挑既有本地 PDF 或来源是本地、又带 DOI 的文献继续判断
                missing=[f for f in("year","venue","volume","pages")  #列出四个关键字段里缺哪个，用生成器表达式逐个检查
                           if not getattr(doc,f,None)]  #用 getattr 安全取字段值，取不到或值为空都算缺失
                if missing or len(doc.authors)<=1:  #只要有任何字段缺失，或者作者人数不超过一个，就认为题录残缺
                    result.append(doc)
                    if limit and len(result)>=limit:
                        break  #立刻跳出循环，避免白扫剩下的大批文献
        return result


__all__=["CleanPipeline","CleanReport","CleanedDocument"]  #模块导出清单：只对外暴露清洗流水线、清洗报告、单篇结果这三个名字
