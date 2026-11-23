"""从 PDF 中提取正文。

优先使用 PyMuPDF，读取失败时退回 pdfminer。提取过程会恢复双栏阅读顺序，
找出跨页重复的页眉页脚，并保留每页对应的字符位置。
"""
from __future__ import annotations

import re  #正则：归一化、页码识别、行尾连字符都要用
from dataclasses import dataclass, field  #用装饰器自动生成初始化与比较方法
from pathlib import Path  #现代路径工具（跨win/Linux通用）
from typing import Any, Dict, List, Optional, Sequence, Tuple  #类型提示专用

from litagent.compat import get_pdf_module  #由'litagent.compat'兼容层去猜可用的PDF模块


# ---------------------------------------------------------------------------
#数据结构
# ---------------------------------------------------------------------------
@dataclass  #装饰器，告诉python这个类的字段自动生成__init__等方法
class PageText:
    """单页抽取结果。"""

    number:int   #页码，从1开始
    text:str   #这一页抠出来的正文
    columns:int=1   #识别出的栏数，只有1和2两种取值，供排查串行问题用
    blocks:int=0   #版面文本块个数，块数异常少往往说明这页是扫描图


@dataclass
class PdfExtraction:
    """一份PDF的完整抽取结果。"""

    path:str=""
    page_count:int=0   #以PDF自报页数为准，不取'len(pages)'：解析中断时两者会不一致
    pages:List[PageText]=field(default_factory=list)   #让每个实例各有新列表，避免共享
    metadata:Dict[str,Any]=field(default_factory=dict)   #同样的道理，字典也不能当默认值直接写
    method:str=""   #实际生效的方法：'pymupdf'/'pdfminer'，都失败时置空表示没抓到
    errors:List[str]=field(default_factory=list)   #失败只往里追加说明，不抛异常打断整批
    removed_lines:List[str]=field(default_factory=list)   #被当成页眉页脚删掉的行，留着便于复盘误删

    @property
    def fulltext(self)->str:
        """全文（页与页之间用空行分隔）。"""
        return "\n\n".join(p.text for p in self.pages if p.text.strip())   #空页不参与拼接，否则会出现多余空行

    @property
    def ok(self)->bool:
        return bool(self.fulltext.strip())   #只看有没有正文，'errors'里有没有记录不影响这个判断

    def page_at_offset(self,offset:int)->Optional[int]:
        """给定全文字符偏移，反查它落在第几页（1起）。

        用于给检索命中的片段标注「PDF第N页」，写论文时对页码很有用。
        """
        cursor=0
        for page in self.pages:
            length=len(page.text)+2  #页间分隔的两个换行，'fulltext'里实际多出来的就是这2个字符
            if offset<cursor+length:   #偏移落在本页区间内就返回本页页码
                return page.number
            cursor+=length   #否则整体右移一页的长度
        return self.pages[-1].number if self.pages else None   #偏移越界就兜底给最后一页，一页都没有则返回'None'


# ---------------------------------------------------------------------------
#正则
# ---------------------------------------------------------------------------
#: 页码正则等常量之后定义：页眉页脚所在的页面上下边距带，占页面高度的比例。
#: 12% 是经验值——多数期刊排版的页眉/页脚都在这个范围内。
MARGIN_BAND_RATIO=0.12

#: 纯页码行，如 "12" / "- 12 -" / "Page 12 of 30" / "第 12 页"
_PAGE_NUMBER_RE=re.compile(   #四个分支用'|'并列，命中任一即判定为纯页码行，开头结尾都锚死
    r"^\s*(?:[-–—\s]*\d{1,4}[-–—\s]*|page\s*\d+(?:\s*of\s*\d+)?|"
    r"第\s*\d+\s*页(?:\s*/\s*共\s*\d+\s*页)?)\s*$",
    re.I,   #忽略大小写，'page'和'Page'都能认
)

#: 行尾连字符断词：字母 + 连字符 + 行尾
_HYPHEN_BREAK_RE=re.compile(r"([A-Za-z])[-–]\s*\n\s*(?=[a-z])")   #两处'[:alpha:]'必须同时成立，光看连字符会把'well-known'拆坏

#: 常见的页眉页脚特征词（命中则优先判定为页眉页脚）
_HEADER_FOOTER_HINT_RE=re.compile(
    r"(?:https?://|www\.|doi:|©|\bcopyright\b|downloaded\s+from|"
    r"all\s+rights\s+reserved|"
    r"第\s*\d+\s*卷|vol\.?\s*\d+|no\.?\s*\d+|issn|isbn)",
    re.I,
)


#: 判定「这行基本就是页码」的两个约束：非数字字符数上限，以及数字占比下限。
#:
#: 必须两条同时满足，只靠其中一条都会误伤：
#:
#: - 只看长度：``"unique 0"``、``"Table 3"``、``"4.1 Method"`` 这类短行会被抹平，
#:   于是 ``4.1 Method`` 与 ``4.2 Method`` 变成同一行、正文被当页眉删掉。
#: - 只看占比：``"page 3 of 10"`` 的数字占比只有 25%，单看占比会漏掉它。
#:
#: 两个例子对照：
#:   ``"page 3 of 10"``  -> 非数字 9（<=12）、占比 3/12=0.25（>=0.25）-> 抹平 ✓
#:   ``"4.1 Method"``    -> 非数字 8（<=12）、占比 2/10=0.20（<0.25） -> 保留 ✓
_PAGE_LIKE_MAX_NONDIGIT=12
_PAGE_LIKE_MIN_DIGIT_RATIO=0.25


def _normalize_for_comparison(line:str)->str:
    """把一行压成可比对的形态，用于识别跨页重复的页眉页脚。

    **数字只在「这行基本就是页码」时才被抹平**。早期版本无条件把数字换成
    ``#``，后果是 ``L0 paragraph one ...`` 与 ``L1 paragraph one ...``
    归一化后完全相同，于是**正文被当成跨页重复的页眉删掉**。
    现实中同样会打中 ``Figure 1: Results`` / ``Figure 2: Results``、
    带编号的小节标题这类内容。

    页码必须抹平是因为它逐页变化（``Page 3`` 与 ``Page 4`` 应当视为同一行），
    但这类行的特征很明确：去掉数字后几乎不剩什么，且数字占比高。
    """
    text=(line or "").strip().lower()
    text=re.sub(r"\s+"," ",text)   #连续空白压成一个空格，两侧版式差异不该影响比对

    digits=sum(1 for ch in text if ch.isdigit())   #统计数字字符个数
    nondigits=len(text)-digits   #非数字字符由总数减出来，不用再遍历一遍
    if(digits>0 and nondigits<=_PAGE_LIKE_MAX_NONDIGIT   #条件1：含数字且非数字不多；条件2：数字占比够高，两条须同时满足
            and digits>=len(text)*_PAGE_LIKE_MIN_DIGIT_RATIO):
        text=re.sub(r"\d+","#",text)   #只在这一种情况下抹平数字，其余一律原样保留

    text=re.sub(r"[^\w\s#\u4e00-\u9fff]","",text)   #去掉标点但保留中文，中文不是'\w'，漏掉就会把中文行整行清空
    return text.strip()


# ---------------------------------------------------------------------------
#主抽取：PyMuPDF
# ---------------------------------------------------------------------------
def _cluster_columns(blocks:Sequence[Tuple[float,float,float,float,str]],
                     page_width:float)->int:
    """判断页面是几栏。返回1或2。

    判据：存在明显的中缝——即没有block的x区间跨越页面中线，且左右两侧
    都有成规模的文本块。
    """
    if not blocks or page_width<=0:   #没有文本块，或页面宽度不可信，都不能做判断
        return 1   #早退：默认按单栏走，宁可少排一栏也不能乱排
    middle=page_width/2.0   #中线就是页面宽度的一半，两栏的分界大致落在这里
    # 允许 6% 的页面宽度作为中缝容差
    tolerance=page_width*0.06   #给中缝留容差，块边界压线时不算成跨界
    left=[b for b in blocks if b[2]<=middle+tolerance]   #用右边界'x1'判断：整块都在中线左边才算左栏
    right=[b for b in blocks if b[0]>=middle-tolerance]   #用左边界'x0'判断，两块都容差放行
    # 只有左右两侧都占到相当比例，才认为是双栏
    if len(left)>=2 and len(right)>=2 and len(left)+len(right)>=len(blocks)*0.7:   #三个条件：1、左右各至少2块；2、覆盖七成以上的块
        return 2   #满足才敢判双栏：只按中线硬切，单栏论文的整行会被拦腰截断
    return 1


def _order_blocks(blocks:Sequence[Tuple[float,float,float,float,str]],
                  columns:int,page_width:float)->List[str]:
    """按阅读顺序输出block文本。

    单栏按上到下；双栏按「先左栏上到下，再右栏上到下」。
    """
    if not blocks:
        return[]
    if columns==1:
        ordered=sorted(blocks,key=lambda b:(round(b[1],1),round(b[0],1)))
    else:
        middle=page_width/2.0   #还是拿中线分左右，判据换成块中心而不是块边界
        left=[b for b in blocks if(b[0]+b[2])/2.0<middle]   #块中心落在线左边就归左栏
        right=[b for b in blocks if(b[0]+b[2])/2.0>=middle]   #剩下的全给右栏，保证一块都不丢
        left.sort(key=lambda b:(round(b[1],1),round(b[0],1)))   #两栏各自纵向排序
        right.sort(key=lambda b:(round(b[1],1),round(b[0],1)))
        ordered=left+right

    texts:List[str]=[]
    for block in ordered:
        text=(block[4] or "").strip()   #第5个元素是块文本，可能为'None'
        if text:   #空块直接丢弃，不留下空行
            texts.append(text)
    return texts   #各调用方约定用换行拼成整页，这里只负责挑出文本


def _collect_margin_lines(blocks:Sequence[Tuple[float,float,float,float,str]],
                          page_height:float,*,band:float=MARGIN_BAND_RATIO
                          )->List[str]:
    """收集位于页面上/下边距带里的文本行（即页眉页脚的候选）。

    **为什么按几何位置而不是「阅读顺序的前几行」**：双栏论文的阅读顺序是
    「左栏自上而下，再右栏自上而下」，于是「前 3 行」会同时包含左栏和右栏的
    开头——那多半是正文，不是页眉。而页眉的定义本就是「位于页面顶端边距带、
    通常横跨整页宽度的那一行」。按 y 坐标取才是对的，且单栏双栏通吃。

    早期实现用的是 ``_order_blocks(blocks, 1, 1.0)``（写死单栏、页码宽度传 1.0），
    与最终渲染所用的 ``_cluster_columns`` 不一致，双栏文档下会把两栏的正文首行
    也纳入「疑似页眉」，一旦这些行在多页上碰巧相同就会被误删。
    """
    if page_height<=0:   #页面高度不可信就算不出边距带，宁可一条不收
        return[]   #早退：返回空列表，调用方自然认为没有页眉，不会误删正文
    top_limit=page_height*band   #上边距带的下沿
    bottom_limit=page_height*(1.0-band)   #下边距带的上沿

    lines:List[str]=[]
    for block in blocks:
        _,y0,_,y1,text=block[0],block[1],block[2],block[3],block[4]   #只取上下边界和文本，x坐标这一层用不上
        if y0<top_limit or y1>bottom_limit:   #条件：1、块顶越过上沿；2、块底越过下沿，压线一块都不放过
            lines.extend(ln.strip() for ln in(text or "").split("\n") if ln.strip())   #一个块可能横跨多行，按行拆开再逐行去空
    return lines   #只返回候选行，删不删由后面的复现率说了算


def _detect_repeated_lines(zone_lines:List[List[str]],*,
                           min_pages:int=3,
                           ratio:float=0.5)->set:
    """从各页的**边距带文本**中找出跨页重复出现的行（页眉页脚）。

    入参应当是 :func:`_collect_margin_lines` 的输出（每页一个列表），
    而不是整页的正文行——只看边距带可以避免误删正文里重复出现的小节标题。

    ``min_pages`` 控制「多少页以下不做检测」。这里取 3 而不是更大的值，
    是因为**短文档同样有页眉**：三五页的会议论文、快报很常见，残留的页眉会
    混进每一个正文片段，既污染 BM25 词表，也可能让用户在溯源时命中页眉
    而不是正文。

    阈值计算为 ``max(2, int(页数 * ratio))``，因此：

    - 3 页 -> 需在 2 页的边距带出现
    - 4 页 -> 需在 2 页出现
    - 10 页 -> 需在 5 页出现

    换言之文档越短要求越严（3 页文档必须 2/3 页都有），
    这恰好是「运行页眉」的特征，误删正文的风险很低。

    **必须传整个列表**（哪怕页数不足 ``min_pages``）：函数内部要按
    ``int(页数 * ratio)`` 算阈值，只看单页是算不出来的。早期实现在调用侧
    先判断「页数是否够」，那样 3 页文档就永远得不到处理。
    """
    if len(zone_lines)<min_pages:   #页数太少不做检测，残留页眉比误删正文划算
        return set()

    counter:Dict[str,int]={}   #键是归一化后的行，值是它出现在几页的边距带里
    for lines in zone_lines:
        # 同一页里重复出现的行只算一票
        keys={_normalize_for_comparison(line) for line in lines}   #转成集合去重：同一页里同一行出现多次不能把页数刷上去
        for key in keys:
            # 太短的行（如 "a"）没有辨识度，跳过
            if len(key)>=4:   #4个字符以下太容易撞车，拿去当页眉依据会误删正文
                counter[key]=counter.get(key,0)+1   #不在字典里就按0起算，等价于'defaultdict(int)'
 
    threshold=max(2,int(len(zone_lines)*ratio))   #页数乘比例再取整，下限2：只出现一页的行不可能是页眉
    return{key for key,count in counter.items() if count>=threshold}   #达到阈值的挑出来交给调用方剔除


def extract_with_pymupdf(path:Path,*,strip_headers:bool=True,
                         strip_page_numbers:bool=True)->PdfExtraction:
    """用PyMuPDF抽取。这是首选方法（速度快、版面信息全）。"""
    result=PdfExtraction(path=str(path),method="pymupdf")   #先按首选方法落名，后面失败再改写
    fitz=get_pdf_module()   #为什么要绕'compat'一层：这个库改过名（老版本'fitz'、新版本'pymupdf'），让兼容层去猜，本文件不直接'import fitz'
    if fitz is None:   #两个导入名都不可用，说明环境里根本没装
        result.errors.append("PyMuPDF 不可用（pymupdf / fitz 均导入失败）")
        result.method=""   #清空方法名，上层看到空串就知道没抓到文本，该去降级
        return result   #早退：不留半成品结果

    try:
        pdf=fitz.open(str(path))
    except Exception as exc:  # noqa: BLE001
        result.errors.append("打开失败：%s: %s"%(type(exc).__name__,str(exc)[:150]))   #截到150字符，异常自带的整篇堆栈会撑爆日志
        result.method=""
        return result

    try:   #保护区：从这里到'finally'任何一句抛异常，文件句柄都会关掉
        result.page_count=pdf.page_count   #以PDF自报页数为准，取'len(pages)'拿不到解析中途失败的那部分
        raw_meta=pdf.metadata or{}   #元数据可能整个是'None'，先兜成空字典才能安全'.get'
        result.metadata={
            k:(raw_meta.get(k) or "").strip()   #取不到和取到空串都按无值处理，值统一去首尾空格
            for k in("title","author","subject","keywords","creator","producer")   #只留这6个字段，其余原样丢弃
            if(raw_meta.get(k) or "").strip()   #值为空就整条不写，省得下游到处判空
        }
        if pdf.is_encrypted:   #加密PDF要先过认证，不过这一步后面取页会直接抛异常
            # 空密码能解的加密 PDF 仍可读
            try:
                pdf.authenticate("")   #很多论文只设了所有者密码，空密码放行，不该拦
            except Exception:  # noqa: BLE001
                result.errors.append("PDF 已加密，无法读取正文")

        raw_pages:List[Tuple[int,float,float,List[Tuple[float,float,float,float,str]]]]=[]   #先把全部页攒起来：要算重复页眉必须看完所有页的边距带，不能边抽边删
        for index in range(pdf.page_count):
            page=pdf[index]
            width=float(page.rect.width) or 1.0   #宽度为0会让后面的栏判断除零，兜成1.0
            height=float(page.rect.height) or 1.0   #同样的道理，高度为0就算不出边距带
            blocks=[]
            for block in page.get_text("blocks"):
                # block = (x0, y0, x1, y1, text, block_no, block_type)
                if len(block)<5:   #元素不足5个连坐标都读不齐，直接丢弃
                    continue
                if len(block)>=6 and block[6]!=0:   # 0 = 文本块，1 = 图片块   #图片块没有文字，留着只会污染正文
                    continue
                text=(block[4] or "").strip()
                if not text:   #空块的坐标不该进入栏判断，否则会把左右两栏都撑大
                    continue
                blocks.append((float(block[0]),float(block[1]),
                               float(block[2]),float(block[3]),text))   #只留(x0,y0,x1,y1,text)五元组，后面所有几何判断都只认这五个位置
            raw_pages.append((index+1,width,height,blocks))   #页码按人类习惯从1记，索引从0会与'PageText.number'对不上

        # 页眉页脚候选：只取页面上下边距带里的行，按几何位置判定（见函数注释）
        margin_lines:List[List[str]]=[
            _collect_margin_lines(blocks,height)   #每页收一份候选，页与页之间不能混
            for _,_,height,blocks in raw_pages
        ]

        repeated=(_detect_repeated_lines(margin_lines)   #必须先看完所有页再统一判重复，逐页边抽边删会永远凑不齐阈值
                    if strip_headers else set())   #关掉页眉剥离就给个空集合，后面判断自然不命中

        for(number,width,_height,blocks) in raw_pages:   #第二遍才真正排序出文本，高度到这一步已经用不上了
            columns=_cluster_columns(blocks,width)
            ordered=_order_blocks(blocks,columns,width)   #再按「列→行」排成阅读顺序
            text="\n".join(ordered)   #块与块之间用换行拼，行尾连字符的合并要的正是这个换行

            kept:List[str]=[]
            for line in text.split("\n"):
                stripped=line.strip()
                if not stripped:
                    kept.append("")   #空行照旧留着，段落之间的空行是有信息的
                    continue
                key=_normalize_for_comparison(stripped)
                if strip_headers and key in repeated:   #命中跨页重复的页眉页脚
                    result.removed_lines.append(stripped)   #原始行留档，误删了能查出来是哪一行
                    continue   #丢弃
                if strip_page_numbers and _PAGE_NUMBER_RE.match(stripped):   #边距带没覆盖到的页码，这里再拦一道
                    continue   #丢弃：页码进正文对检索只有坏处
                kept.append(stripped)

            page_text="\n".join(kept)
            page_text=_HYPHEN_BREAK_RE.sub(r"\1",page_text)   #只删连字符不删换行，保留的行边界由下面统一收拾
            page_text=re.sub(r"\n{3,}","\n\n",page_text).strip()   #3个以上换行压成1个空行，顺手去掉整页首尾空白
            result.pages.append(
                PageText(number=number,text=page_text,
                         columns=columns,blocks=len(blocks))   #把栏数与块数一并记下，页数异常时能直接定位到是哪一页排版特殊
            )
    finally:
        pdf.close()   #无论成败都关句柄，不然批量处理时文件会被锁死

    return result


# ---------------------------------------------------------------------------
#降级：pdfminer
# ---------------------------------------------------------------------------
def extract_with_pdfminer(path:Path)->PdfExtraction:
    """pdfminer.six降级方案。版面还原能力弱于PyMuPDF，但容错性好。"""
    result=PdfExtraction(path=str(path),method="pdfminer")   #先按降级方法落名，导入失败时再清空
    try:
        from pdfminer.high_level import extract_text as _extract_text  # type: ignore[reportMissingImports]  #放在'try'里导入：没装pdfminer时能被'ImportError'接住，不影响主链路
    except ImportError as exc:   #只接'ImportError'，别的报错不该被当成"没装"
        result.errors.append("pdfminer 不可用：%s"%exc)
        result.method=""   #和PyMuPDF那边口径一致：方法名置空表示没抓到
        return result

    try:
        text=_extract_text(str(path)) or ""
    except Exception as exc:  # noqa: BLE001
        result.errors.append("pdfminer 抽取失败：%s: %s"%(
            type(exc).__name__,str(exc)[:150]))   #同样截150字符，异常详情只留够定位的部分
        result.method=""
        return result

    # pdfminer 用 \f 分页
    chunks=text.split("\f")   #换页符是它唯一的分页线索，不切就等于把全文当成一页
    for index,chunk in enumerate(chunks,start=1):   #页码从1起，与PyMuPDF那一路保持一致
        cleaned=re.sub(r"[ \t]+"," ",chunk)   #连续空格压成一个，pdfminer为了对齐会塞大量空格
        cleaned=_HYPHEN_BREAK_RE.sub(r"\1",cleaned)   #共用同一条断词正则，两条路径抠出来的文本口径才一致
        cleaned=re.sub(r"\n{3,}","\n\n",cleaned).strip()   #空行归一化和PyMuPDF那边同处理
        result.pages.append(PageText(number=index,text=cleaned))   #降级路径拿不到块坐标，栏数就按默认的单栏记
    result.page_count=len(result.pages)   #分页失败时PDF自报页数不可信，以实际切出来的页数为准
    return result


# ---------------------------------------------------------------------------
#对外入口
# ---------------------------------------------------------------------------
def extract_pdf(path:Any,*,strip_headers:bool=True,
                strip_page_numbers:bool=True,
                min_chars_per_page:float=50.0,
                allow_fallback:bool=True)->PdfExtraction:
    """抽取PDF正文，返回:class:`PdfExtraction`。

    若PyMuPDF抽出的文本过少（平均每页不足``min_chars_per_page``字），
    说明很可能是**扫描件**或字体编码异常，此时自动降级到pdfminer重试，
    取两者中字符数更多的结果。

    真正的扫描件（纯图片PDF）两种方法都无解，会在``errors``里留提示，
    交由上层决定是否做OCR（本项目不含OCR）。
    """
    extraction=extract_with_pymupdf(
        Path(path),strip_headers=strip_headers,strip_page_numbers=strip_page_numbers
    )   #先走首选方法，走到这一步还没抛异常就说明文件本身至少能打开

    pages=max(1,extraction.page_count or 1)   #页数兜成至少1，否则下面求密度会除零
    density=len(extraction.fulltext)/float(pages)
    needs_fallback=(not extraction.ok) or(density<min_chars_per_page)   #两个触发条件：1、一个字都没抽到；2、字太少，多半是扫描件或字体编码异常

    if needs_fallback and allow_fallback:   #允许降级时再试第二条路
        fallback=extract_with_pdfminer(Path(path))
        if len(fallback.fulltext)>len(extraction.fulltext):   #只有降级结果更长才采用，否则保留原来的，避免越降越差
            # 保留 PyMuPDF 拿到的更完整的元数据
            if extraction.metadata and not fallback.metadata:   #降级把元数据弄丢了就用原来那份补上
                fallback.metadata=extraction.metadata
            fallback.errors=extraction.errors+fallback.errors   #两条路的问题记录合并，原来的在前，排查时先看首选方法为什么失败
            extraction=fallback   #整体换掉，方法名也就跟着变成'pdfminer'

    #降级结果可能换了页数或正文长度，必须按最终采用的 extraction 重算密度。
    pages=max(1,extraction.page_count or 1)
    density=len(extraction.fulltext)/float(pages)
    if not extraction.ok:   #两条路都没抠到字，基本可以断定是纯图片PDF
        extraction.errors.append(
            "抽取结果为空：可能是扫描件（纯图片 PDF），需要 OCR，本项目不含 OCR 功能"
        )   #只留提示不抛异常：本项目不含OCR，抛错也解决不了，交给上层决定
    elif density<min_chars_per_page:   #抽到字但密度仍然偏低，属于能用但要提醒的情况
        extraction.errors.append(
            "文本密度偏低（%.0f 字/页），可能混入扫描页或解析不完整"%density
        )   #密度低不等于失败，所以只在'errors'里标注，不改变'ok'的判定
    return extraction


def looks_like_scanned(extraction:PdfExtraction,*,min_chars_per_page:float=50.0)->bool:
    """判断是否像扫描件。"""
    pages=max(1,extraction.page_count or 1)   #同样兜成至少1页，防除零
    return len(extraction.fulltext)/float(pages)<min_chars_per_page   #判据与'extract_pdf'里触发降级的条件保持一致，免得两处口径不同

__all__=[   #声明'from litagent.clean.pdf_text import *'时要导入哪些名字
    "PageText",   #单页结果
    "PdfExtraction",   #整份PDF的抽取结果
    "extract_pdf",
    "extract_with_pymupdf",   #首选方法，单独导出便于测试时直接指定
    "extract_with_pdfminer",   #降级方法，同上
    "looks_like_scanned",   #扫描件判断，供上层决定要不要提示OCR
]
