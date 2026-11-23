"""把论文切成带章节和页码的检索片段。

章节名会影响检索和总结，因此不能只按固定字数切分。这里尽量在段落
或句子边界结束片段，并保留字符区间到 PDF 页码的映射。
"""
#从未来导入注解
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from litagent.clean.normalize import estimate_tokens
from litagent.schema import SECTION_ALIASES, Chunk, content_hash, has_cjk

#三个正则一大类，用长横线隔出视觉分区，比空行更容易扫读
#标题识别规则
#下面每个正则都只管「这行像不像标题」，认章节名交给另一个函数
#冒号开头的是预处理正则：只要命中就说明这行长度范围合适、不是文献条目
#西文编号标题
_LATIN_HEADING_RE=re.compile(
    r"^\s*(?:\d{1,2}(?:\.\d{1,2}){0,3}|[IVXLC]{1,5})[.)]?\s+"
    r"([A-Z][A-Za-z][^.!?;:]{1,70})\s*$"
)

#无编号标题最危险，'Abstract' 和正文里一个普通短行长得一模一样，所以只能靠别名表反向确认
#西文无编号标题
_LATIN_BARE_RE=re.compile(r"^\s*([A-Z][A-Za-z ]{2,40})\s*$")

#中文标题的编号形态比西文多得多：顿号、括号、第X章都要覆盖，漏一种就漏一节
#中文编号标题
_CN_HEADING_RE=re.compile(
    r"^\s*(?:第[一二三四五六七八九十]+[章节]|[一二三四五六七八九十]+[、.]|"
    r"（[一二三四五六七八九十]+）|\([一二三四五六七八九十]+\)|"
    r"\d{1,2}(?:\.\d{1,2}){0,3}[、.\s])\s*(\S[^\n]{0,40})\s*$"
)

#双栏论文的章节标题常常整行大写且无编号，这一条专门捞这种
#全大写标题
_UPPER_HEADING_RE=re.compile(r"^\s*([A-Z][A-Z0-9 \-]{3,40})\s*$")

#文献条目形如「[12] 作者. 题目」，它同样短，不先排掉就会被当成标题
#排除文献条目
_LOOKS_LIKE_REFERENCE_RE=re.compile(r"^\s*(?:\[\d+\]|\d{1,3}[.)])\s+\S")


#阿拉伯数字前缀允许 1.2.3.4 四级，再多就是正文里的公式编号了
#编号前缀
_NUMBER_PREFIX_RE=re.compile(r"^\d{1,2}(?:\.\d{1,2}){0,3}[.)]?\s*")  #剥离时用 'sub' 而不是 'lstrip'：编号和标题之间可能没有空格
#全大写形式的「III. RESULTS」是论文章节标题的常见写法
#只写小写字符集会让这类标题的编号剥离不掉、进而识别不出章节，所以必须加 're.I'
#罗马数字前缀
_ROMAN_PREFIX_RE=re.compile(r"^[ivxlc]{1,5}(?:[.)]\s*|\s+)",re.I)  #分隔符强制要求，且允许「III.」和「III 」两种收尾写法

#中文前缀与上面的西文前缀分开写，因为中文标点是全角、不能用同一套字符集
#中文编号前缀
_CN_PREFIX_RE=re.compile(  #全角顿号、半角点、空格都算分隔符，缺一个就会有标题剥不干净
    r"^(?:第[一二三四五六七八九十]+[章节]|[一二三四五六七八九十]+[、.]|"
    r"（[一二三四五六七八九十]+）|\([一二三四五六七八九十]+\)|\d{1,2}(?:[.\d]*)[、.\s])\s*"
)


def _strip_heading_number(heading:str)->str:
    """去掉标题前的编号，返回纯标题文本。
    罗马数字前缀必须带分隔符（'.'、')' 或空白）才剥离。早期版本写成
    '^[ivxlc]+[.)]?\\s*'——分隔符是可选的，于是任何以 i、v、x、l、c 开头的英文单词
    都会被削掉首字母：'introduction' 变 'ntroduction'、'conclusion' 变 'onclusion'，
    导致这两类章节永远识别不出来，整篇论文的片段全被归到默认章节里。
    """
    text=heading.strip()
    #逐级剥离编号
    text=_NUMBER_PREFIX_RE.sub("",text)  #三级编号依次剥离：编号可能同时含西文和中文前缀，所以不能只剥一次
    text=_ROMAN_PREFIX_RE.sub("",text)  #罗马数字放在阿拉伯数字之后：'1.1' 这种编号里不会出现罗马字符，先后其实不影响结果，但这样写更直观
    text=_CN_PREFIX_RE.sub("",text)  #中文前缀最后剥，因为它可能与前两级重叠，例如「第3章 3.1 方法」
    return text.strip(" .:-—\u3001")  #收尾再统一裁掉残留的分隔符，防止「. Introduction」这种剥不干净的名字进别名表


def _canonical_section(heading:str)->Optional[str]:
    """把标题文本映射到标准章节名；无法识别返回 'None'。
    三级匹配依次放宽：先精确、再前缀、最后词边界包含，越往后越容易误命中。
    """
    text=_strip_heading_number(heading).lower()
    if not text:  #剥完编号后可能是空串（标题行只有一个编号），提前早退，省掉后面三轮遍历
        return None

    # 1) 精确匹配
    #第一级是精确匹配
    if text in SECTION_ALIASES:  #第一级：标题与别名完全一致，最可靠也最省事
        return SECTION_ALIASES[text]  #命中即返回，不再往下走，避免后面的宽松匹配覆盖掉精确结果

    # 2) 前缀匹配（"related work and background" -> related_work）
    #第二级是前缀匹配
    for alias,canonical in SECTION_ALIASES.items():  #第二级：标题以别名开头。'related work and background' 该归到 'related_work'
        if len(alias)<3 or not text.startswith(alias):  #别名短于 3 个字符直接跳过，否则 'a' 这种别名会命中一大片
            continue  #命中后还要看剩下的尾巴是什么，光看开头不够
        rest=text[len(alias):]  #取别名之后剩下的部分，用来判断是不是复数形式
        # 允许复数后缀："reference" 应命中 "References"
        if rest in("","s","es"):  #只认空串、's'、'es' 三种尾巴：'reference' 该命中 'References'，但 'referencement' 不该
            return canonical  #尾巴合规才认，这样前缀匹配既宽松又不会一路吃到底

    # 3) 词边界包含匹配（"experiments and results" -> results）
    #
    # 必须用 \b，并显式允许复数后缀：
    #   - 朴素的子串包含会让 "DATA AND TASK ABSTRACTION" 里的 "abstract"
    #     命中，把讲「抽象」的小节整节误判成摘要；
    #   - 但单纯的 \b 又会让 "References" / "Limitations" 匹配不上单数别名。
    # 「词边界 + 可选复数」同时解决这两点。
    #
    # 中文走单独分支：CJK 没有词边界概念，且单字信息量远高于单个拉丁字母，
    # 所以中文别名降到 2 字即可参与包含匹配（"研究方法" -> method），
    # 拉丁别名仍要求 4 个字符以上，避免过宽的误命中。
    #第三级是包含匹配
    for alias,canonical in SECTION_ALIASES.items():
        if has_cjk(alias):  #中文和西文走两套阈值，因为两种文字的「一个字符」信息量根本不是一个量级
            if len(alias)>=2 and alias in text:  #中文别名降到 2 字即可参与包含匹配，因为「研究方法」这种词本身就够具体
                return canonical  #命中即返回，中文别名短、误命中风险高，所以一命中就收手不再往下试
        elif len(alias)>=4 and re.search(r"\b%s(?:s|es)?\b"%re.escape(alias),text):  #用词边界加可选复数，避免把 'abstraction' 里的 'abstract' 当成摘要章节
            return canonical  #西文别名要求 4 个字符以上：'uses' 这种三字母别名会把大半个正文都吃掉
    return None


def detect_sections(text:str)->List[Tuple[int,str,str]]:
    """识别章节标题。
    返回 '[(字符偏移, 标题原文, 标准章节名)]'，按出现顺序排列；
    识别不出任何标题时返回空列表，由调用方兜底成默认章节。
    """
    if not text:
        return[]  #空文本没有标题可认，直接早退，别让后面的循环白跑
    #逐行过筛
    sections:List[Tuple[int,str,str]]=[]  #每个元素是（起始字符偏移、标题原文、标准章节名）
    offset=0  #手动逐行累加偏移，比事后在原文里 'find' 更稳，重复标题也不会找错位置
    seen_positions=set()  #记录已认过的偏移，防止同一行被两条规则重复认成标题

    for line in text.split("\n"):
        line_length=len(line)
        stripped=line.strip()

        # 标题必然短
        #先排掉文献条目
        if 2<=len(stripped)<=80 and not _LOOKS_LIKE_REFERENCE_RE.match(stripped):  #条件一：长度在 2~80 之间，标题必然短；条件二：这行不像文献条目
            heading:Optional[str]=None
            for pattern in(_LATIN_HEADING_RE,_CN_HEADING_RE):  #带编号的标题先试：西文编号和中文编号的形态互斥，命中一个就跳出
                match=pattern.match(stripped)  #逐个正则去试，命中即停，避免同一行被两条正则各认一次
                if match:
                    heading=match.group(1).strip()  #只取第 1 个捕获组：编号前缀用来定位，真正的标题文本才是要的部分
                    break  #命中即跳出，后面的正则不用再试
            if heading is None:  #带编号的都没命中，才退一步试「无编号标题」
                match=_LATIN_BARE_RE.match(stripped)
                if match:  #无编号标题的误判率最高，必须先过一道「能不能映射到已知章节名」的关
                    candidate=match.group(1).strip()  #取捕获组并去空白，'Abstract' 这类纯单词标题就靠这一步拿到
                    #映射不出来就当它是正文短行，宁漏不错：错认标题会把正文整段挂到错误的章节上
                    if _canonical_section(candidate):
                        heading=candidate  #确认是标题文本，赋给变量统一走下面的登记流程

            if heading:  #标题文本有了，还要能映射到标准章节名才值得登记
                canonical=_canonical_section(heading)  #映射成标准名，检索层只认标准名，标题原文只做展示
                if canonical and(offset not in seen_positions):  #条件一：能映射；条件二：这个偏移还没登记过，两个都满足才收
                    #同一章节名最多认 4 次：正文里「方法」这种小节标题会反复出现
                    count=sum(1 for _,_,name in sections if name==canonical)  #数一数这个标准名已经登记过几次，超过 4 次就视为重复小标题丢掉
                    if count<4:  #没到上限才登记，防止把 20 个「实验设置」小标题当成 20 个章节
                        sections.append((offset,heading,canonical))  #登记（偏移、原文、标准名）三元组，顺序即文档顺序
                        seen_positions.add(offset)  #记下偏移，供上一行的去重判断使用

        offset+=line_length+1  #加 1 是补回被 'split' 吃掉的换行符，不加就会让后面的偏移逐行累积偏差

    return sections


def _split_paragraphs(text:str,base_offset:int)->List[Tuple[str,int]]:
    """按空行切段落，返回 '[(段落文本, 相对偏移)]'。
    段落是分块的天然边界：块只在段落之间断，就不会出现半句话的片段。
    """
    #按空行切段
    paragraphs:List[Tuple[str,int]]=[]
    cursor=0
    for block in re.split(r"\n\s*\n",text):  #连续空行（含只含空格的行）都算一个分隔；用正则一次切完，比手写状态机省事
        if not block.strip():  #切出来的空块是连续空行的产物，跳过它
            cursor+=len(block)+2
            continue  #空块也要推进游标：它占的字符数加上被吃掉的换行，否则下一块会找错位置
        #块文本可能被正则裁掉了首尾空白，要在原文里找回它的真实起点
        position=text.find(block,cursor)  #从游标开始往后找，避免同一段文字重复出现时找到前面那个
        if position<0:  #真找不到就退回游标位置，宁可偏移差一点，也不能让整段丢失
            position=cursor
        paragraphs.append((block.strip(),base_offset+position))  #存的是去空白后的文本，但要配原文里的偏移，两者分工不同
        cursor=position+len(block)  #游标推到本块末尾，保证下一块一定在它之后找
    return paragraphs


#重叠取尾
def _overlap_tail(buffer:str,overlap_chars:int)->str:
    """取片段尾部的重叠文本，并对齐到词边界。
    直接 'buffer[-n:]' 会把一个词甚至一个汉字从中间切开，例如
    「large lan|guage models」的重叠部分会以「guage models…」开头。
    这样的残词会作为噪声进入词频统计，也会污染溯源时的上下文回显，
    更要命的是两个块各持半个词，谁都没法被整词检索命中。
    这里向后找到最近的空白或标点，从其后的完整词开始。
    """
    if overlap_chars<=0 or not buffer:  #重叠长度为 0，或缓冲区本来就是空的，没有尾巴可取
        return ""
    if len(buffer)<=overlap_chars:  #缓冲区不比重叠长，那就整段都当重叠，再多也变不出来
        return buffer

    tail=buffer[-overlap_chars:]  #先按字符数硬切出候选尾巴，下面再往右挪到自然边界
    #只在重叠区前 1/3 内找边界：找得太靠后会把重叠压成几个字，失去重叠的意义
    window=max(1,len(tail)//3)  #空白和常见中英文句末标点都算边界，取第一个命中的
    boundary=re.search(r"[\s。！？；;.!?]\s*",tail[:window])  #从边界之后开始取，这样尾巴的第一个字符一定是完整词的词首
    if boundary:
        return tail[boundary.end():]
    #整段没有标点时退回到最近的空白处，仍然保住词不被切开
    index=tail.find(" ")  #只找空白，是因为中文没有空格，能落到这里的多半是连续英文
    if 0<=index<window:  #空白落在窗口内才采用，否则等于把重叠几乎全丢光
        return tail[index+1:]  #连空白都没有，只能原样返回：宁可重叠长一点，也不切词
    return tail


DEFAULT_MAX_CHARS=1800


#超长段落拆句
def _split_long_paragraph(text:str,max_chars:int)->List[str]:
    """把超长段落按句子边界切成多段。
    只处理「一整段就超过上限」的情况：不先拆句的话，这种段落会被原样当成一块，
    整块超长时既没法被整段命中，也不利于片段级的溯源定位。
    """
    limit=max(1,int(max_chars))
    if len(text)<=limit:  #没超上限就原样返回，绝大多数段落走这条快路径
        return[text]

    #中文标点后通常没有空格，英文句点则只在上文像句末时切，
    #避免把小数点、缩写和普通标点当成句子边界。
    sentence_re=re.compile(r"(?<=[。！？；;])|(?<=[.!?])(?=\s+(?:[A-Z0-9“\"'(\[]))")
    sentences=[part.strip() for part in sentence_re.split(text) if part.strip()]

    def hard_split(value:str)->List[str]:
        """没有标点的超长段落按字符上限兜底切开，优先落在空格或中文标点后。"""
        parts:List[str]=[]
        while len(value)>limit:
            window=value[:limit]
            cuts=[window.rfind(ch) for ch in (" ","，","、","：",":","\n")]
            cut=max(1,min(limit,max(cuts)+1))
            if cut<limit*0.6:
                cut=limit
            parts.append(value[:cut].strip())
            value=value[cut:].strip()
        if value:
            parts.append(value)
        return parts

    pieces:List[str]=[]  #装好的成品块先攒在这里，句子是逐句往缓冲区里塞的
    buffer=""  #当前正在攒的句子缓冲区，攒到装不下就吐出去
    for sentence in sentences:
        for part in hard_split(sentence):
            if buffer and len(buffer)+len(part)+1>limit:  #加 1 是预估拼接时的分隔符
                pieces.append(buffer)  #装不下就先把已攒的吐出去
                buffer=part
            else:
                buffer=(buffer+" "+part).strip() if buffer else part
    if buffer:  #首句直接赋值，不做拼接，免得块首多出两个换行
        pieces.append(buffer)  #循环收尾，剩下没吐的必须补一次，否则最后一段永远进不了结果
    return pieces or hard_split(text)


def _page_for_offset(offset:int,page_offsets:Optional[Sequence[Tuple[int,int]]]
                     )->Optional[int]:
    """把字符偏移映射到页码，'page_offsets' 是 '[(起始偏移, 页码)]'。
    取「最后一个不超过该偏移的起点」对应的页码，偏移越小页码越小，保持单调。
    """
    if not page_offsets:  #没有页码信息就返回 'None'，调用方会当作「页码未知」处理
        return None
    page=page_offsets[0][1]  #默认取第一页的页码：偏移落在第一个页面起点之前时，它仍然属于第一页
    for start,number in page_offsets:  #顺序遍历，一旦当前起点超过偏移就说明区间已确定，后面的不用再看
        if offset>=start:  #偏移不小于起点，先记下这一页，继续往后找更贴近的
            page=number  #遇到第一个超过偏移的起点就跳出，省掉剩余遍历
        else:
            break
    return page


#主入口
def chunk_text(
    doc_id:str,
    text:str,
    *,
    max_chars:int=1800,
    overlap_chars:int=200,
    min_chars:int=50,
    page_offsets:Optional[Sequence[Tuple[int,int]]]=None,
)->List[Chunk]:
    """把正文切成有序片段：先按章节切区间，再在区间内按段落装块、超限就吐出。
    参数：
    'max_chars' 单个片段的目标上限（字符）。1800 字约合 450~600 个英文 token，
    既能提供足够上下文，又不至于长到影响检索与阅读；
    'overlap_chars' 相邻片段的重叠字符数，避免答案正好落在切口处；
    'min_chars' 小于此长度的片段直接丢弃，通常是页眉残留或孤立的图表标题；
    'page_offsets' 来自 PDF 抽取结果的 '[(字符偏移, 页码)]'，用于给片段标注页码。
    """
    if not text or not text.strip():  #全空白或空串直接返回空列表，让调用方不必再判一次
        return[]

    #公开入口也做独立防护：即使调用方绕过 Settings 直接传 0/负数，
    #也不会形成不推进的循环。配置层另外会把越界值回退到 1800。
    try:
        max_chars=int(max_chars)
    except(TypeError,ValueError,OverflowError):
        max_chars=DEFAULT_MAX_CHARS
    if max_chars<=0:
        max_chars=DEFAULT_MAX_CHARS
    try:
        overlap_chars=max(0,int(overlap_chars))
    except(TypeError,ValueError,OverflowError):
        overlap_chars=0
    overlap_chars=min(overlap_chars,max(0,max_chars-1))
    try:
        min_chars=max(1,int(min_chars))
    except(TypeError,ValueError,OverflowError):
        min_chars=1
    min_chars=min(min_chars,max_chars)

    #先认章节
    sections=detect_sections(text)  #先认章节，拿到的偏移就是天然的切块边界
    # 构造 [(起始偏移, 章节名, 标题)]
    boundaries:List[Tuple[int,str,Optional[str]]]=[]  #构造边界列表，元素是（起始偏移、章节名、标题）
    if sections:  #有章节标题时才需要补「摘要段」这个虚拟章节
        # 正文开头到第一个标题之间（通常是摘要）
        if sections[0][0]>0:  #第一个标题之前的正文通常是摘要；它没有标题行，所以标题位放 'None'
            boundaries.append((0,"abstract",None))  #先补一条偏移 0 的边界，否则摘要会被并进第一个真正的章节里
        for index,(offset,heading,canonical) in enumerate(sections):
            boundaries.append((offset,canonical,heading))  #登记（偏移、标准名、标题原文）三元组
    else:  #一个标题都没认出来时，整篇当作一节 'body'，保证碎片仍然能出块
        boundaries.append((0,"body",None))  #兜底边界：偏移 0、章节名 'body'、没有标题行

    #相邻边界围出的区间就是各章节的正文，章节区间一旦定死，块就不会跨章节
    ranges:List[Tuple[int,int,str,Optional[str]]]=[]
    #算出各章节的区间
    for index,(offset,canonical,heading) in enumerate(boundaries):
        end=boundaries[index+1][0] if index+1<len(boundaries) else len(text)  #末节的右端点取正文总长，否则最后一节会被整节丢掉
        if end>offset:  #右端点不大于左端点说明是空区间，跳过，免得后面切出空片段
            ranges.append((offset,end,canonical,heading))  #存齐四元组：起点、终点、标准名、标题，装块时都要用

    #按区间装块
    chunks:List[Chunk]=[]  #成品块按吐出的先后顺序攒在这里
    ordinal=0  #全局连续序号，跨章节也不重置，id 和序号都依赖它

    for start,end,canonical,heading in ranges:
        section_text=text[start:end].strip()
        if len(section_text)<min_chars:  #章节太短就整节丢弃：通常是页眉残留或孤立的图表标题
            continue
        # 标题行本身留在片段开头，检索时能带上标题词
        body_start=start+(len(text[start:end])-len(text[start:end].lstrip()))  #左端空白数 = 原始长度减去掉左边空白后的长度，用它把起点推到标题行上

        paragraphs=_split_paragraphs(text[start:end],body_start)
        buffer=""  #跨段落的拼装缓冲区，一段一句地往里塞，塞满就吐
        buffer_start=body_start  #缓冲区的起始偏移，吐块时用它反算字符区间和页码

        #吐出一个块
        def flush()->None:
            nonlocal buffer,ordinal  #flush 会改缓冲区和序号；buffer_start 只读取外部值
            piece=buffer.strip()  #先去掉两端空白再判长度，避免把只含空白的块写进索引
            if len(piece)>=min_chars:
                chunk_end=buffer_start+len(buffer)  #块右端 = 起点 + 缓冲区长度，注意用的是未去空白的长度，保持区间连续
                chunks.append(Chunk(  #组装块对象，下面逐个字段补齐
                    id=Chunk.make_id(doc_id,ordinal,piece),  #id 由文档号、序号和文本三者派生，三者任一变化 id 就变，天然幂等
                    doc_id=doc_id,  #文档号原样带上，检索时要靠它回查是哪篇论文
                    text=piece,  #存去空白后的文本，这才是真正入库、被检索的内容
                    ordinal=ordinal,  #序号与 id 用的是同一个值，两者必须同进同退
                    section=canonical,  #章节标准名，检索层做章节过滤就靠它
                    heading=heading,  #标题原文带上，检索命中时能展示「来自方法一节」这种可读信息
                    page_start=_page_for_offset(buffer_start,page_offsets),  #起始页按块的起点算，起点落在哪一页就是哪一页
                    page_end=_page_for_offset(max(buffer_start,chunk_end-1),  #结束页按块尾前一个字符算，防止块尾恰好压在页边界上时多算一页
                                              page_offsets),  #起始偏移直接就是缓冲区起点
                    char_start=buffer_start,
                    char_end=chunk_end,  #字符区间存的是未去空白的长度，与上面的一对页码口径保持一致
                    token_count=estimate_tokens(piece),  #token 数交给归一化模块估算，分块阶段不引入额外的分词依赖
                    content_hash=content_hash(doc_id,str(ordinal),piece),  #内容哈希带上序号做盐，同一段文字出现在不同位置时不会撞成同一个哈希
                ))
                ordinal+=1  #序号自增放在吐块成功之后：太短的块被丢弃时不该占用序号
            buffer=""  #清空缓冲区，下一段从零开始攒

        #逐段装进缓冲区
        for paragraph,position in paragraphs:
            for piece in _split_long_paragraph(paragraph,max_chars):  #段落先过一道超长拆句，保证装进来的一句不会单独就超上限
                if not buffer:  #缓冲区为空说明要开新块，直接放进空缓冲，不做拼接
                    buffer=piece  #开新块时把起点定在这一段的位置上，块首就是段落首
                    buffer_start=position  #已经装了这一句，后面的循环从头开始，不重复装
                    continue
                if len(buffer)+len(piece)+2>max_chars:  #加 2 是留给两个换行分隔符，先留余量再判，避免拼出来刚好超一点点
                    tail=_overlap_tail(buffer,overlap_chars)  #装不下才取重叠尾巴，装得下就不取，省掉一次正则扫描
                    flush()  #先吐掉当前块，注意顺序：必须在取完尾巴之后再吐，否则尾巴会被清空
                    room=max(0,max_chars-len(piece)-1)  #重叠尾巴不能把片段顶过上限；piece 本身已保证不超
                    tail=tail[-room:] if room else ""
                    buffer=(tail+"\n"+piece) if tail else piece  #重叠部分接在新块开头，让答案落在切口处时两边都能命中
                    buffer_start=max(0,position-len(tail))  #新块起点要减去尾巴长度，否则字符区间与实际内容对不上、页码也会错
                else:
                    buffer=buffer+"\n\n"+piece  #装得下就用两个换行拼上，段落之间保留空行，读起来仍是分段的
        flush()  #循环收尾必须补一次 flush，否则最后攒着的一块永远吐不出来

    return chunks


#页码表
def page_offsets_from_pages(pages:Sequence[Any])->List[Tuple[int,int]]:
    """从 'PageText' 列表算出 '[(偏移, 页码)]'。
    必须与实际拼接方式严格一致：全文是用两个换行拼接各页文本得到的，
    所以这里也按同样的分隔符累加长度，否则偏移与正文错位、页码整体偏移一页。
    """
    #累加偏移
    offsets:List[Tuple[int,int]]=[]
    cursor=0
    for page in pages:
        text=getattr(page,"text","") or ""  #属性取不到时给空串，页面对象的字段命名在各抽取后端并不统一
        number=getattr(page,"number",0)  #页码取不到时给 0，代表「未标页码」，比 'None' 更好参与后续计算
        offsets.append((cursor,number))  #本页的起点偏移就是当前游标，先登记再累加，顺序反了会整体错一页
        cursor+=len(text)+2  #加 2 是补回拼接时插入的两个换行，与全文拼接方式严格对齐
    return offsets


#统计函数
def chunk_stats(chunks:Sequence[Chunk])->Dict[str,Any]:
    """分块统计，供清洗报告展示。
    空列表也要返回同样键名的零值字典，免得调用方到处补 'None' 判断。
    """
    if not chunks:  #空列表提前返回，键名要和下面完全一致，调用方才不用判空
        return{"count":0,"chars":0,"tokens":0,"avg_chars":0,"sections":{}}
    total_chars=sum(len(c.text) for c in chunks)  #总字符数按块的文本长度累加，用的是入库后的真实长度
    total_tokens=sum(c.token_count for c in chunks)  #总 token 数直接取块上已算好的值，不在这里重新估算，避免口径不一致
    sections:Dict[str,int]={}  #按标准章节名分组计数，用来回答「哪一节被切得最多」
    for chunk in chunks:
        sections[chunk.section]=sections.get(chunk.section,0)+1  #同一章节的计数加一，第一次见到这个章节名时从 0 起步
    return{
        "count":len(chunks),  #块数直接取列表长度
        "chars":total_chars,  #字符总数，用于判断分块是否把内容完整覆盖了一遍
        "tokens":total_tokens,  #token 总数，用于和模型的上下文上限做对比
        "avg_chars":int(total_chars/len(chunks)),  #取整平均长度，报告里展示成整数更直观
        "sections":dict(sorted(sections.items(),key=lambda kv:-kv[1])),  #章节计数按数量降序，报告里最长的章节排最前面，方便一眼看出偏斜
    }


#对外接口
__all__=[
    "detect_sections",
    "chunk_text",  #章节识别，单独暴露出去便于调试和单测
    "chunk_stats",  #主入口，调用方只需要这一个函数
    "page_offsets_from_pages",  #统计函数，供清洗报告直接调用
]  #页码表构造函数，抽 PDF 时与主入口配对使用
