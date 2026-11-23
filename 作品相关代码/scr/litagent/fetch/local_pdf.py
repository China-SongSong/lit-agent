"""扫描本地文献目录。

支持 PDF、TXT、Markdown 和 DOCX。PDF 在这一步只取元数据，正文留给
清洗流程读取。CAJ、WPS 和加密 PDF 会跳过并记录原因。
"""
from __future__ import annotations  #导入未来特性，让类型注解延迟求值，避免循环导入时报错。

import re  #导入标准库 re，后面用它处理文件名和 Word 的 XML。
import zipfile  #导入标准库 zipfile，因为 docx 本质就是一个压缩包。
from pathlib import Path  #导入 Path，用它跨平台地表示和拼接文件路径。
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from litagent.compat import get_pdf_module  #导入兼容层函数，用来取 PDF 解析模块，没装时会返回空值。
from litagent.fetch.base import Source, register_source
from litagent.schema import (  #导入文档模型，以及内容指纹、DOI 规范化、作者解析三个工具。
    Document,
    content_hash,
    normalize_doi,
    parse_authors,
)

#注释：下面是支持的后缀定义。
PDF_SUFFIXES={".pdf"}  #定义 PDF 后缀集合；用集合判断比列表快，而且自动去重。
TEXT_SUFFIXES={".txt",".md",".markdown",".text"}
DOCX_SUFFIXES={".docx"}
SUPPORTED_SUFFIXES=PDF_SUFFIXES|TEXT_SUFFIXES|DOCX_SUFFIXES  #用竖线把三个集合合并成总集合，判断受不受支持只需一次。

#注释：下面是扫描时要跳过的目录名，避免把依赖或备份里的文件也扫进来。
SKIP_DIR_NAMES={  #开始定义要跳过的目录名集合。
    ".git",".svn",".hg","__pycache__","node_modules",".venv","venv",  #集合内容：版本库目录、缓存目录和虚拟环境目录都要跳过。
    ".deps",".tmp","$recycle.bin","system volume information",  #继续列举依赖目录、临时目录，以及 Windows 回收站等目录名。
}  #跳过目录名集合定义结束。

#注释：下面是用正则从 PDF 首页文本里抓 DOI 的表达式。
_DOI_RE=re.compile(r"\b10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+",re.I)  #预编译 DOI 正则：反斜杠 b 表示词边界，末尾的 re.I 表示忽略大小写。

#注释：下面几个正则是用来解析 Word 文档 XML 的。
_DOCX_PARA_RE=re.compile(r"</w:p>")  #段落结束标签的正则，用来把整篇 XML 按段落切开。
_DOCX_TEXT_RE=re.compile(r"<w:t[^>]*>(.*?)</w:t>",re.S)  #正文文本标签的正则，问号表示非贪婪，re.S 让点号也能匹配换行。
_DOCX_TAB_RE=re.compile(r"<w:tab[^>]*/>")  #制表符标签的正则，用来把 Word 的制表位还原成制表符。
_DOCX_BR_RE=re.compile(r"<w:br[^>]*/>")  #强制换行标签的正则，用来还原段落内部的手动换行。


def _decode_bytes(data:bytes)->str:  #定义内部函数：把文件字节按顺序尝试多种编码，解成字符串。
    """尽力解码文本文件：UTF-8 → UTF-8-BOM → GBK → Latin-1 兜底。"""
    for encoding in("utf-8-sig","utf-8","gb18030","gbk","big5"):  #五种编码各有分工：BOM 版管记事本文件，UTF-8 管普通文件，GB18030 管简体中文，GBK 是它的旧名，BIG5 管繁体。
        try:  #开 try：解码失败会抛异常，交给 except 去试下一种编码。
            return data.decode(encoding)  #用当前编码解码，成功就立刻返回，后面的编码不再尝试。
        except(UnicodeDecodeError,LookupError):  #捕获解码失败与编码名不存在两种异常，少捕获一种都会让整批中断。
            continue  #这一种解不开就跳过本轮，换下一种编码继续试。
    return data.decode("utf-8",errors="replace")  #五种都失败时才用 UTF-8 加替换模式兜底，坏字节变问号，保证不抛异常。


def read_docx(path:Path)->str:  #定义读 docx 的函数，返回整篇纯文本，任何失败都返回空串。
    """从 .docx 抽取纯文本（不依赖 python-docx，直接读 zip 里的 XML）。

    **段落结构必须保住**：分块器依赖空行/换行来识别段落边界，如果把整篇
    粘成一行，分块就只能按字数硬切，而且章节标题也识别不出来了。

    早期实现先 ``re.sub("</w:p>", "\\n")`` 把段落标记换成换行，然后只
    ``findall("<w:t>...</w:t>")`` 再把结果用空串拼接——换行符位于
    ``</w:t>`` 与 ``<w:t>`` **之间**，不在任何 ``<w:t>`` 内部，于是全部丢失，
    整篇文档变成一行。正确做法是**先按段落切开，再在段内取文本**。
    """
    try:  #开 try：压缩包损坏或路径不存在都要当成空结果，不能往外抛。
        with zipfile.ZipFile(path) as archive:  #用 zipfile 以压缩包方式打开 docx，with 保证用完自动关闭文件。
            names=archive.namelist()  #列出压缩包里所有条目的名字，用来找正文那个文件。
            target="word/document.xml"  #docx 的正文默认固定在 word 目录下的 document.xml。
            if target not in names:  #如果默认路径不在清单里，说明是别的工具生成的变体文档。
                target=next((n for n in names if n.endswith("document.xml")),"")  #兜底找出第一个以 document.xml 结尾的条目，一个都没有就用空串。
            if not target:  #一个都没找到说明这根本不是真正的 Word 文档。
                return ""  #直接返回空串，让上层按没有正文来处理。
            xml_bytes=archive.read(target)  #读出正文 XML 的原始字节，此刻它还是压缩状态。
    except(zipfile.BadZipFile,OSError,KeyError):  #捕获坏压缩包、系统读写错误和键不存在三种异常，坏文件不拖垮整批。
        return ""  #出错就返回空串，调用方拿到空文本自然会跳过这个文件。

    xml_text=xml_bytes.decode("utf-8",errors="replace")  #XML 按规范一定是 UTF-8，个别坏字节用替换模式变成问号。

    def unescape(value:str)->str:
        return(value.replace("&lt;","<").replace("&gt;",">")
                .replace("&quot;",'"').replace("&apos;","'").replace("&amp;","&"))  #继续还原引号和单引号；和号必须最后换，否则会把前面的还原结果再改坏。

    def paragraph_text(fragment:str)->str:
        #注释：段落内部的手动换行和制表符要保留，不能被丢掉。
        fragment=_DOCX_BR_RE.sub("\n",fragment)
        fragment=_DOCX_TAB_RE.sub("\t",fragment)
        return "".join(unescape(p) for p in _DOCX_TEXT_RE.findall(fragment)).strip()  #取出段内所有文本片段，逐个还原实体后拼接，最后去掉首尾空白。

    #注释：先按段落边界切分，再在每段内部收集文本，换行才不会丢。
    lines=[paragraph_text(chunk) for chunk in _DOCX_PARA_RE.split(xml_text)]  #用段落结束标签切分整篇 XML，再对每一段调用取文本的函数。
    lines=[line for line in lines if line]  #过滤掉空段落，只留下有内容的段落。
    #注释：连续多个空段落压成一个空行，保持段落之间空一行的常见排版。
    text="\n".join(lines)  #用换行把段落拼回整篇文本，段落边界因此保留下来。
    return re.sub(r"\n{3,}","\n\n",text).strip()  #用正则把三个及以上连续换行压成两个，并去掉首尾空白。


def extract_pdf_metadata(path:Path)->Tuple[Dict[str,Any],Optional[str]]:  #定义读 PDF 元数据的函数，返回元数据字典和首页文本两部分。
    """读取 PDF 内嵌元数据与首页文本中的 DOI。

    返回 ``(元数据字典, 首页文本或 None)``。PyMuPDF 不可用时返回空元数据。
    这里**不做全文抽取**——全文是清洗阶段（``litagent.clean.pdf_text``）的职责，
    避免抓取阶段扫描大量文件时过慢。
    """
    meta:Dict[str,Any]={}
    first_page:Optional[str]=None  #首页文本先设成空值，表示暂时还没有。
    fitz=get_pdf_module()  #通过兼容层拿 PDF 解析模块，变量名沿用了 PyMuPDF 的旧名字。
    if fitz is None:  #如果没装 PDF 库，就降级处理，而不是让整个程序崩掉。
        return meta,None

    try:  #开 try：损坏或加密的 PDF 不能让整批扫描中断。
        with fitz.open(str(path)) as pdf:  #打开 PDF 文件；转成字符串是为了兼容老版本库对参数的要求。
            meta["page_count"]=pdf.page_count  #记录总页数，后面给文档对象填页数字段时要用。
            raw=pdf.metadata or{}  #读取内嵌元数据；库可能返回空值，用空字典兜底。
            for key in("title","author","subject","keywords","creator","producer"):
                value=(raw.get(key) or "").strip()  #取值并去掉首尾空白，字段缺失时当作空串。
                if value:  #只有真的读出内容才写进结果，避免留下一个空键。
                    meta[key]=value
            if pdf.page_count:  #有页数才去读第一页，零页文档读页会直接报错。
                first_page=pdf[0].get_text("text") or None  #读第一页纯文本；扫描版 PDF 没有文字层，这里会得到空值。
            meta["encrypted"]=bool(pdf.is_encrypted)  #记录这份 PDF 是否加密，转成布尔值方便后面判断。
    except Exception as exc:  # noqa: BLE001
        meta["error"]="%s: %s"%(type(exc).__name__,str(exc)[:120])  #把异常类型和截断到一百二十个字符的消息记进元数据，方便排查。
    return meta,first_page


def guess_title_from_filename(path:Path)->str:
    """文件名兜底当作标题：去掉扩展名，把下划线/连字符换成空格。"""
    stem=path.stem  #取不含扩展名的那部分文件名。
    stem=re.sub(r"[_\-]+"," ",stem)
    stem=re.sub(r"\s+"," ",stem).strip()  #再用反斜杠 s 匹配连续空白并压成一个空格，顺便去掉首尾空白。
    return stem or path.name  #万一处理完是空串，就退回带扩展名的完整文件名，保证标题不为空。


def extract_doi_from_text(text:Optional[str])->Optional[str]:  #定义从一段文本里抓 DOI 的函数，抓不到就返回空值。
    """从文本里抓第一个 DOI。"""
    if not text:  #文本为空或为空值时，没必要去跑正则。
        return None  #直接返回空值，表示这段文本里没有 DOI。
    match=_DOI_RE.search(text[:6000])  #只在前六千个字符里搜索，首页信息够用，而且更快。
    if not match:  #判断有没有匹配到 DOI。
        return None  #返回空值，表示这份文献没能读到 DOI。
    return normalize_doi(match.group(0))  #取匹配到的整段文字做规范化，比如统一格式、去掉多余前缀。


@register_source  #注册装饰器：把这个类登记进数据源表，命令行就能按名字找到它。
class LocalPdfSource(Source):
    """本地文献库扫描器。

    与其他数据源不同，它的主要 API 是 :meth:`scan`（按路径发现文件），
    ``search`` 仅实现按文件名匹配，以保持接口一致。
    """

    name="local"  #数据源的名字叫 local，配置和日志里用的就是这个名字。
    default_rate=1000.0  #默认速率设成每秒一千次，注释说明本地磁盘不需要限流。
    supports_search=False  #声明不支持关键词检索，检索这件事交给索引层。
    supports_doi=False  #声明不支持按 DOI 抓取，本地文件没有这个概念。

    #注释分隔线，把文件发现部分与后面的代码分开。
    #注释小标题：下面是文件发现相关代码。
    #又一条注释分隔线，给文件发现这一节收尾。
    @staticmethod  #静态方法装饰器：这个函数不用实例状态，不必先建对象。
    def discover(paths:Sequence[Any],*,recursive:bool=True,  #定义发现文件的函数；星号后面的参数只能用关键字传，避免位置传错。
                 suffixes:Optional[Iterable[str]]=None)->List[Path]:  #后缀参数可选，不传就用全局的支持后缀集合。
        """展开路径列表，返回去重后的受支持文件（按路径排序，结果稳定）。"""
        wanted={s.lower() for s in(suffixes or SUPPORTED_SUFFIXES)}
        found:List[Path]=[]
        seen:set=set()  #用集合记录已见路径的指纹，用来去重。

        for raw in paths:  #逐个处理用户给的每一个路径。
            path=Path(raw).expanduser()  #转成 Path 并展开用户主目录的那种简写写法。
            if path.is_file():  #用户直接指到某个具体文件的情况也要支持。
                if path.suffix.lower() in wanted:  #是文件就检查后缀是否在受支持集合里。
                    key=str(path.resolve()).lower()  #取绝对路径的小写形式当去重指纹，同一文件的不同写法只算一个。
                    if key not in seen:  #只有没见过的路径才继续往下走。
                        seen.add(key)
                        found.append(path)
                continue  #单个文件处理完就换下一个路径，不再走后面的目录逻辑。
            if not path.is_dir():  #既不是文件也不是目录，说明这个路径写错了或不存在。
                continue  #直接跳过而不报错，方便一次给一长串路径。

            iterator=path.rglob("*") if recursive else path.glob("*")  #递归为真时用 rglob 钻进子目录，否则只扫一层。
            for candidate in iterator:  #遍历扫到的每一个候选条目。
                if not candidate.is_file():  #只要文件，目录本身不收；注意这里没有文件大小上限，超大文件也会被读进来。
                    continue  #不是文件就跳过，继续看下一个条目。
                if candidate.suffix.lower() not in wanted:  #后缀不在受支持集合里的直接不要。
                    continue  #跳过不支持的类型，比如 CNKI 的 caj 和 wps。
                if any(part.lower() in SKIP_DIR_NAMES for part in candidate.parts):  #路径里任意一段命中跳过清单就整条丢掉，依赖和备份目录因此被排除。
                    continue  #跳过这些目录里的文件，免得把依赖目录中的东西收进语料库。
                if candidate.name.startswith("~$"):  #Word 打开文档时会生成以波浪号加美元符开头的临时文件，必须跳过。
                    continue  #跳过这种临时文件。
                key=str(candidate.resolve()).lower()  #同样用绝对路径的小写形式当去重指纹。
                if key in seen:  #软链接或重复路径导致又扫到同一个文件时。
                    continue  #这个文件已经收过，跳过。
                seen.add(key)
                found.append(candidate)

        found.sort(key=lambda p:str(p).lower())  #按路径字符串的小写形式排序，保证每次扫描的顺序都一样。
        return found

    @staticmethod  #静态方法装饰器：下面的文本转换不需要实例状态。
    def _from_text_file(path:Path)->Optional[Document]:  #把文本类文件读成文档对象，失败时返回空值。
        try:  #开 try：文件可能被占用、权限不足或中途消失。
            text=_decode_bytes(path.read_bytes())  #先读原始字节再交给多编码解码器，先字节后解码是关键顺序。
        except OSError:  #捕获系统读写错误。
            return None  #读不了就放弃这一个文件，不影响别的文件。
        if not text.strip():  #整篇都是空白字符的文件没有价值。
            return None  #空文件不值得生成文档，直接放弃。
        title=guess_title_from_filename(path)  #文本文件没有内嵌元数据，只能用文件名当标题。
        doi=extract_doi_from_text(text)  #从正文里试着抓一个 DOI，抓不到也没关系。
        doc=Document(  #开始构造统一的文档对象。
            id=Document.make_id("local",str(path),doi,title),  #用源名、路径、DOI 和标题一起算出稳定的本地文档编号。
            doi=doi,  #把 DOI 填进去，抓不到时为空。
            title=title,
            authors=[],  #文本文件抽不出作者，留空列表。
            venue_type="unknown",  #出处类型未知，本地文件看不出是期刊还是会议。
            url=None,  #本地文件没有网址。
            language="zh" if any("\u4e00"<=ch<="\u9fff" for ch in text[:2000]) else "en",  #前两千字里找到汉字就当中文，否则当英文；用码点区间判断比装语言库轻。
            fulltext=text,
            source="local",  #来源字段标记成本地。
            source_id=str(path),  #用文件路径当源内编号，方便回溯到具体文件。
            is_oa=True,  #本机文件视为可以自由使用的开放获取。
            pdf_path=str(path),  #记下文件路径，后面清洗阶段还要按它再读一遍。
            content_hash=content_hash(text[:200000]),  #只取前二十万字符算内容指纹，取全文会拖慢速度而且没有必要。
            extra={"file_path":str(path),"file_type":path.suffix.lower().lstrip(".")},  #附加信息里存路径和类型，后缀用 lstrip 去掉前面的点号。
        )  #文档构造参数到此结束。
        return doc

    @classmethod  #类方法装饰器：下面按类调用就行，也方便子类覆盖。
    def _from_pdf(cls,path:Path)->Optional[Document]:  #把 PDF 文件转成文档对象。
        meta,first_page=extract_pdf_metadata(path)
        if meta.get("error") and meta.get("page_count") is None:  #有错误又没有页数，说明这个文件根本打不开。
            #注释：打不开的文件也入库，但打上标记，方便人工排查。
            title=guess_title_from_filename(path)  #坏文件只能用文件名当标题。
            return Document(  #构造一个最小文档并立即返回，不走后面的正常流程。
                id=Document.make_id("local",str(path),None,title),  #编号里 DOI 传空值，打不开的文件本来就读不到 DOI。
                title=title,
                venue_type="unknown",  #出处类型标为未知。
                source="local",  #来源字段标记成本地。
                source_id=str(path),  #源内编号用文件路径。
                pdf_path=str(path),  #记下 PDF 路径，后面还要用它。
                language="en",  #猜不出语言，先按英文处理。
                extra={"file_path":str(path),"file_type":"pdf",  #附加信息里写文件路径和类型。
                       "error":meta.get("error")},
            )  #这个最小文档构造结束。

        title=(meta.get("title") or "").strip()  #取内嵌标题并去掉空白，字段缺失时当作空串。
        if not title or title.lower() in("untitled","microsoft word","document"):  #标题为空或是那几个默认垃圾值时，说明这份元数据不可信。
            title=guess_title_from_filename(path)  #改用文件名当标题。

        doi=extract_doi_from_text(first_page)  #从首页文本里抓 DOI，很多论文会把 DOI 印在首页。
        authors=parse_authors(
            [a for a in re.split(r"[;,]| and ",meta.get("author") or "") if a.strip()]
        )  #作者解析的调用到此结束。

        doc=Document(  #开始构造正常的 PDF 文档对象。
            id=Document.make_id("local",str(path),doi,title),  #用源名、路径、DOI 和标题算出稳定编号。
            doi=doi,  #把抓到的 DOI 填进文档对象里。
            title=title,
            authors=authors,
            venue_type="unknown",  #出处类型标为未知。
            language="en",  #语言先默认英文，稍后再按首页文字纠正。
            source="local",  #来源字段标记成本地。
            source_id=str(path),  #源内编号用文件路径。
            is_oa=True,  #本机文件视为开放获取。
            pdf_path=str(path),  #记下 PDF 路径，供清洗阶段再读全文。
            page_count=meta.get("page_count"),
            extra={  #附加信息字典开始。
                "file_path":str(path),  #附加信息里存文件路径。
                "file_type":"pdf",  #把文件类型标记成 pdf。
                "pdf_metadata":meta,
                "first_page_doi":doi,  #单独记一份首页抓到的 DOI。
                "encrypted":meta.get("encrypted",False),  #记录是否加密，键不存在时当作没有加密。
            },  #附加信息字典到这里结束。
        )  #文档对象的构造参数到此结束。
        if any("\u4e00"<=ch<="\u9fff" for ch in(first_page or "")[:2000]):  #首页前两千字里出现汉字就把语言改成中文。
            doc.language="zh"  #覆盖成中文，说明这是一篇中文文献。
        return doc

    def scan(self,paths:Sequence[Any],*,recursive:bool=True,
             suffixes:Optional[Iterable[str]]=None,  #后缀过滤参数可选。
             on_progress:Optional[Any]=None)->List[Document]:  #进度回调可选，用于界面或命令行显示进度。
        """扫描路径，返回 Document 列表（已设置 ``pdf_path``）。

        ``on_progress`` 可选，签名 ``(已完成数, 总数, 当前文件)``，用于打印进度。
        """
        files=self.discover(paths,recursive=recursive,suffixes=suffixes)
        documents:List[Document]=[]
        total=len(files)  #总数先算好，进度回调要用。

        for index,path in enumerate(files,start=1):
            suffix=path.suffix.lower()  #统一取小写后缀，后面的分支判断要用。
            try:  #开 try：任何一个文件出错都不能中断整批扫描。
                if suffix in PDF_SUFFIXES:  #PDF 走专门的元数据解析分支。
                    doc=self._from_pdf(path)  #调用 PDF 转换函数拿到文档对象。
                elif suffix in DOCX_SUFFIXES:  #Word 分支比较特殊，要先用通用文本读取拿基础信息。
                    doc=self._from_text_file(path)  #先按文本文件读一遍，拿到路径、标题等基础信息。
                    if doc is not None:  #读成功才继续做真正的 docx 解析。
                        #注释：通用读取拿到的是压缩包字节，必须换成专用解析。
                        text=read_docx(path)  #用 zip 加 XML 的方式抽出真正的正文。
                        if not text.strip():  #抽不出文字说明文档是空的或格式异常。
                            continue  #直接跳过这个文件，连基础文档也不加进结果。
                        doc.fulltext=text  #用抽出的正文覆盖原来的压缩包字节，不覆盖会污染语料库。
                        doc.content_hash=content_hash(text[:200000])  #正文变了必须重算内容指纹，否则去重和增量更新都会失误。
                        doc.extra["file_type"]="docx"
                        doc.title=doc.title or guess_title_from_filename(path)  #标题为空时才用文件名兜底。
                elif suffix in TEXT_SUFFIXES:  #txt 和 md 直接按文本处理即可。
                    doc=self._from_text_file(path)
                else:  #兜底分支，前面已经过滤过后缀，这里只防意外。
                    continue  #不认识的后缀直接跳过。
            except Exception as exc:  # noqa: BLE001
                doc=Document(  #出错也构造一个文档，把失败原因带出去。
                    id="local:"+str(path),  #出错时编号直接拼路径，这种文档只用于排查。
                    title=guess_title_from_filename(path),  #这时候标题只能用文件名来猜。
                    source="local",  #来源字段标记成本地。
                    source_id=str(path),  #源内编号用文件路径。
                    pdf_path=str(path),  #把文件路径记进文档对象。
                    extra={"file_path":str(path),"error":"%s: %s"%(  #附加信息里记路径和错误原因。
                        type(exc).__name__,str(exc)[:120])},  #错误内容是异常类型加截断到一百二十个字符的说明。
                )  #出错文档的构造结束。

            if doc is not None and not doc.is_empty():  #文档存在且不算空才算有效结果，空文档收进去只会污染语料库。
                documents.append(doc)

            if on_progress is not None:  #有进度回调才调用它。
                try:  #开 try：回调是外部代码，可能抛异常。
                    on_progress(index,total,path)  #报告已完成数、总数和当前正在处理的文件。
                except Exception:  # noqa: BLE001
                    pass  #什么都不做，静默忽略。

        return documents

    #注释分隔线，把扫描部分和接口实现分开。
    #注释小标题：下面是接口实现，只按文件名匹配。
    #接口实现这一节也以注释分隔线收尾。
    def search(self,query:str,*,limit:int=20,offset:int=0,
               paths:Optional[Sequence[Any]]=None,**filters:Any)->List[Document]:  #路径必须由调用方传入，另外收下一堆用不上的过滤参数。
        """按文件名包含关系筛选本地文件（真正的检索请用索引层）。"""
        if not paths:  #没给路径就找不到本地文件。
            return[]  #没有路径就返回空列表。
        needle=(query or "").strip().lower()  #查询词去掉首尾空白并转小写，空查询当作空串。
        documents=self.scan(paths)
        if needle:  #查询词非空才筛选，空查询等于要全部结果。
            documents=[  #开始用列表推导筛选文档。
                d for d in documents  #遍历列表里的每一个文档。
                if needle in d.title.lower()  #标题里含查询词的就保留。
                or needle in str((d.extra or{}).get("file_path","")).lower()  #或者文件路径里含查询词的也保留，方便按目录找文件。
            ]  #列表推导到这里结束。
        return documents[offset:offset+max(1,int(limit))]  #按偏移和条数切片；max 保证至少取一条，避免零或负数把结果切空。


__all__=[
    "LocalPdfSource",  #导出本地数据源这个类。
    "read_docx",  #导出 Word 解析函数，方便别处复用。
    "extract_pdf_metadata",  #导出 PDF 元数据读取函数。
    "guess_title_from_filename",  #导出用文件名猜标题的函数。
    "extract_doi_from_text",  #导出从文本抓 DOI 的函数。
    "SUPPORTED_SUFFIXES",  #导出受支持的后缀集合，命令行帮助里可能会用到。
]  #模块对外导出清单到此结束。
