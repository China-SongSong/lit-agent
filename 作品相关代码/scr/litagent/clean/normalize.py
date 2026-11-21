"""清理 PDF 转出的正文。

处理内容包括行尾断词、多余空格、控制字符、引用编号和末尾参考文献。
这些噪声会让分词与精确匹配失准，因此要在建索引前统一处理。
"""
from __future__ import annotations

import re   #全篇的清洗都是正则驱动的，没有它这层什么都做不了
import unicodedata   #'NFKC'归一化和控制字符判定都靠它，标准库自带不用装
from typing import List,Optional

from litagent.schema import REFERENCE_HEADINGS   #标题词表复用在'litagent.schema'，避免两个模块各写一份慢慢漂移

# ---------------------------------------------------------------------------
# 正则
# ---------------------------------------------------------------------------
#: 行尾连字符断词。英文排版常见："trans-\nformer" -> "transformer"
_HYPHEN_BREAK_RE=re.compile(r"([A-Za-z])[-\u2010\u2011]\s*\n\s*(?=[a-z])")   #三个字符类都限定死：前一个必须是字母、连字符含三种变体（半角加U+2010加U+2011）、后一个必须是小写；不这么卡，页码'5998-\n6008'会被粘成一个数，从句'well-\nknown'的连字符也会被误删

#: CJK 字符之间夹杂的空格（PDF 抽取常见病）
_CJK=r"\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af"   #扩展A区、基本区、日文假名、谚文四段拼成一个字符类，中日韩文本一起兜住，写成一个变量是为了下面三处正则共用同一份范围
_CJK_SPACE_RE=re.compile(r"(?<=[%s])\s+(?=[%s])"%(_CJK,_CJK))   #前后都用零宽断言，只吃掉中间的空格本身，让相邻字符靠拢成连续串
#: CJK 与西文之间的多余空格（保留一个，便于分词）
_CJK_LATIN_SPACE_RE=re.compile(r"(?<=[%s])\s+(?=[0-9A-Za-z])"%_CJK)   #这里是置成一个空格而不是删掉：全删会让'深度学习transformer'粘成一个词，jieba切不开
_LATIN_CJK_SPACE_RE=re.compile(r"(?<=[0-9A-Za-z])\s+(?=[%s])"%_CJK)   #和上一条反方向各写一条，因为前瞻断言只能朝一个方向看

#: 控制字符（保留 \n \t）
_CONTROL_RE=re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")   #区间故意跳过'\x09'和'\x0a'：制表符和换行是后面按行切段要用的结构，删了行就没了

#: 连字（ligature）还原
_LIGATURES={   #PDF的字体编码经常把连字当成一个码位抽出来，不还原的话'efﬁcient'永远搜不到
    "\ufb00":"ff","\ufb01":"fi","\ufb02":"fl","\ufb03":"ffi",
    "\ufb04":"ffl","\ufb05":"st","\ufb06":"st",
    "\u0132":"IJ","\u0133":"ij","\u0152":"OE","\u0153":"oe",
    "\u00c6":"AE","\u00e6":"ae",
}   #字典用'items()'遍历、逐个换而不是用'str.translate'：一次换完可能把刚生成的字符再卷进下一轮替换

#: 软连字符与零宽字符
_INVISIBLE_RE=re.compile(
    r"[\u00ad\u200b\u200c\u200d\u200e\u200f\u2060"
    r"\u202a-\u202e\u2066-\u2069\ufeff]"
)   #零宽、方向控制和双向文本控制符都不可见，不能污染分块与检索

#: 超过 2 个连续换行
_MANY_NEWLINES_RE=re.compile(r"\n{3,}")   #只压到2个：留一个空行才能保住段落的边界，全压成1个就把段落结构抹平了

#: 多个连续空格
_MANY_SPACES_RE=re.compile(r"[ \t\u00a0\u3000]{2,}")   #不换行空格和全角空格一并算进同一类，它们和普通空格在检索侧没有区别

#: 引用标记，如 "[1]" "[1-3]" "[1,3]" "(Smith et al., 2020)"
_BRACKET_CITATION_RE=re.compile(r"\[\s*\d+(?:\s*[-,–]\s*\d+)*\s*\]")   #连字符、逗号、半角与全角破折号都算进分隔符，一段一段往后接，'[1]'、'[1,3]'、'[1-3]'三种写法一条正则全接住

#: 参考文献条目特征：以数字编号或年份开头的短行
_REFERENCE_ENTRY_RE=re.compile(r"^\s*(?:\[\d+\]|\d{1,3}[.)])\s+\S")   #'^'锚在行首、末尾要求跟空白再跟非空白：光有编号没内容的是空条目行，不算数，避免目录页把整段的编号密度刷上去

#: 期刊/会议参考文献里高频出现的词，用于确认「这段确实是参考文献」
_REFERENCE_KEYWORD_RE=re.compile(
    r"\b(?:doi|vol\.?|no\.?|pp\.?|proceedings|conference|journal|"
    r"transactions|press|springer|elsevier|acm|ieee|arxiv|"
    r"出版社|学报|大学|学位论文)\b",
    re.I,   #卷期页码大小写混写（'Vol.'/'VOL.'）都有，忽略大小写才拦得住
)


# ---------------------------------------------------------------------------
# 单步规范化
# ---------------------------------------------------------------------------
def normalize_unicode(text:str,*,form:str="NFKC")->str:   #'*'之后的参数只能用关键字传，避免调用方误传成位置参数
    """Unicode 规范化 + 连字还原。

    用 NFKC 而不是 NFC：NFKC 会把全角字母数字（ＡＢＣ１２３）折成半角，
    把异体标点统一，这对检索更有利。中文字符不受 NFKC 影响。
    """
    if not text:   #空串或'None'直接早退，下面的调用都假定输入非空
        return ""
    text=unicodedata.normalize(form,text)  # type: ignore[reportArgumentType]  #先做整串归一化，顺序在做连字替换之前，否则全角字符换完还要再归一化一次
    for source,target in _LIGATURES.items():
        if source in text:   #纯字符串包含判断，比无条件替换便宜：绝大多数文本一个连字都没有
            text=text.replace(source,target)
    return text


def remove_invisible(text:str)->str:
    """去掉软连字符、零宽字符与控制字符。"""
    if not text:   #空串或'None'直接早退
        return ""
    text=_INVISIBLE_RE.sub("",text)   #先清不可见字符：它们夹在连字符和字母之间会把'fix_hyphenation'的正则挡住，顺序不能换
    text=_CONTROL_RE.sub("",text)   #再清控制字符，'\n'和'\t'由正则里的区间缺口放行
    return text


def fix_hyphenation(text:str)->str:
    """合并跨行断开的英文单词。"""
    if not text:   #空串或'None'直接早退
        return ""
    # 反复替换，处理连续多行都以连字符结尾的情况
    previous=None   #用一个'None'做哨兵：第一轮'None'一定不等于任何字符串，循环至少进一次
    while previous!=text:
        previous=text
        text=_HYPHEN_BREAK_RE.sub(r"\1",text)   #用反向引用'\1'把前一个字母原样留下，只吃掉连字符和换行
    return text   #直到一整轮替换后字符串不再变化才收手


def normalize_cjk_spacing(text:str)->str:
    """修正 CJK 排版空格。

    - CJK 与 CJK 之间的空格全部去掉（「深度 学习」-> 「深度学习」）
    - CJK 与西文之间的空格统一为**一个**（保留，否则 jieba 会把
      「深度学习transformer」当成一个词）
    """
    if not text:   #空串或'None'直接早退
        return ""
    text=_CJK_SPACE_RE.sub("",text)
    text=_CJK_LATIN_SPACE_RE.sub(" ",text)   #再把中文到西文之间压成一个，多个空格会被这一条归一化掉
    text=_LATIN_CJK_SPACE_RE.sub(" ",text)   #西文到中文方向同理，两条都跑完空格就统一了
    return text


def normalize_punctuation(text:str)->str:
    """统一常见标点变体，便于检索匹配。"""
    if not text:   #空串或'None'直接早退
        return ""
    table={   #PDF里的智能引号和全角空白是排版产物，不折成ASCII，用户用键盘敲的引号永远匹配不上
        "\u2018":"'","\u2019":"'","\u201c":'"',"\u201d":'"',
        "\u2013":"-","\u2014":"-","\u2212":"-","\u2010":"-",
        "\u2026":"...","\u00a0":" ","\u3000":" ",
    }   #映射表放在函数体里而不是模块级常量：只有这个函数用得上，就地定义少一个全局名字
    for source,target in table.items():
        if source in text:
            text=text.replace(source,target)
    return text


def collapse_whitespace(text:str)->str:
    """压缩多余空白。"""
    if not text:   #空串或'None'直接早退
        return ""
    text=_MANY_SPACES_RE.sub(" ",text)
    text="\n".join(line.strip() for line in text.split("\n"))   #按'\n'切（不用'splitlines'，它会把'\r\n'也吞掉，破坏行结构），逐行去首尾空白再拼回
    text=_MANY_NEWLINES_RE.sub("\n\n",text)   #行首空白清掉之后必然出现大量空行，最后统一压成单空行
    return text.strip()   #整串首尾的空行一并去掉，返回值可以直接落盘


# ---------------------------------------------------------------------------
# 参考文献段剥离
# ---------------------------------------------------------------------------
def find_references_start(text:str,*,min_ratio:float=0.45)->Optional[int]:
    """定位参考文献段的起始行号，找不到返回 ``None``。

    不能简单地在全文里搜 "references" —— 这个词在摘要、正文里到处都是。
    这里用三重条件锁定真正的参考文献段：

    1. 该行**几乎只有**标题词（``^\\s*references\\s*$`` 这种）
    2. 位置在文档**后半段**（默认 >45% 处）——参考文献总在最后
    3. 该行**之后**的内容符合参考文献特征（大量编号条目或含 doi/vol/pp 等词）

    第 3 条是关键：仅有前两条时，正文里的「5 References」小节标题会被误切。
    """
    if not text:   #空串或'None'直接早退，返回'None'表示找不到
        return None
    lines=text.split("\n")   #按行切开：后面的所有判据都建立在行结构上，所以调用前不能先把换行压掉
    if len(lines)<20:   #太短的文本不可能有参考文献段，直接放行，省掉后面两轮回溯
        return None

    heading_pattern=re.compile(   #标题词表从'litagent.schema'导入而不是在这里写死，词表扩充时全项目一起生效
        r"^\s*(?:\d+[.)]?\s*)?(?:"+
        "|".join(re.escape(h) for h in REFERENCE_HEADINGS)+   #逐个'escape'：词表里有'works cited'这种带空格的项，不转义正则元字符会被当成语法
        r")\s*[:.]?\s*$",
        re.I,   #'References'/'REFERENCES'大小写都有，忽略大小写
    )   #整条正则两头用'^''$'夹死：只有「几乎只剩标题词」的行才算，正文里带'references'的句子匹配不上

    start_index=int(len(lines)*min_ratio)   #'int()'向下取整当起始行，前面45%连看都不看，直接把摘要和正文的命中拦在门外
    candidates:List[int]=[]
    for index in range(start_index,len(lines)):
        if heading_pattern.match(lines[index]):   #'match'从行首匹配，正好配上带'^'的正则
            candidates.append(index)

    if not candidates:   #一条候选都没有，说明没有参考文献段
        return None

    # 从后往前试：参考文献是文档最后一节，越靠后越可能是真的
    for index in reversed(candidates):   #倒序试：命中越靠后越可信，正文里的小节标题一般都在参考文献之前
        following=lines[index+1:]
        if _looks_like_reference_block(following):   #用标题后面的内容做确认，这是三重判据里最关键的一条
            return index
    return None   #有候选但没一个通过内容确认，判定为没有参考文献段，宁可漏切不可错切


def _looks_like_reference_block(lines:List[str])->bool:
    """判断一段文本是否像参考文献列表。"""
    non_empty=[ln for ln in lines if ln.strip()]   #先滤掉空行再统计：不加这一步，PDF抽出的稀疏文本会被空行把比例冲淡
    if len(non_empty)<5:   #少于5行的块样本量太小，随便几行都能凑出比例，直接判否
        return False

    numbered=sum(1 for ln in non_empty if _REFERENCE_ENTRY_RE.match(ln))   #推导式求和统计编号条目数，'sum'对'True'计数天然可用
    keyworded=sum(1 for ln in non_empty if _REFERENCE_KEYWORD_RE.search(ln))   #用'search'不用'match'：特征词出现在行中任意位置都算
    total=len(non_empty)

    # 多数条目以编号开头，或相当比例的条目含期刊/出版社等特征词
    if numbered/total>=0.4:   #编号密度够高就认：条件是「或」，任一成立即可，两条同时要求会把无编号的著者-年份制漏掉
        return True
    if keyworded/total>=0.3:   #期刊词密度这条专门救中文文献和无编号条目
        return True
    # 参考文献条目普遍较短（一行一条）
    short=sum(1 for ln in non_empty if len(ln)<200)   #短行比例当作最后一道补充判据
    return short/total>=0.8 and numbered>=3   #两个条件缺一不可：正文段落也短，必须同时还有实打实的编号条目兜着才敢认


def strip_references(text:str)->str:
    """切掉参考文献段。"""
    start=find_references_start(text)
    if start is None:   #没定位到就原样返回，宁可不切也不乱切
        return text
    return "\n".join(text.split("\n")[:start]).rstrip()   #切片取前'start'行再拼回，末尾'rstrip'去掉切完留下的一串空行


# ---------------------------------------------------------------------------
# 引用标记
# ---------------------------------------------------------------------------
def strip_inline_citations(text:str,*,keep:bool=False)->str:
    """去掉正文里的 ``[1]`` ``[12]`` ``[1-3]`` 这类数字引用标记。

    默认**保留**（``keep=True`` 为默认行为由调用方决定）：这些标记对
    「这段引用自哪篇」的溯源其实有信号价值。这里提供能力，是否启用交给上层。
    """
    if not text or keep:   #空文本或要求保留，直接原样返回：判定放前面，省掉一次正则扫描
        return text
    return _BRACKET_CITATION_RE.sub(" ",text)   #换成空格而不是空串：'模型[1]在'要切成两个词，直接删会把前后词粘成一个


# ---------------------------------------------------------------------------
# 组合入口
# ---------------------------------------------------------------------------
def clean_text(text:str,*,strip_refs:bool=True,   #'*'之后的开关只能用关键字传：几个布尔参数连着写位置传参，看调用点分不清谁是谁
               strip_citations:bool=False,   #默认不删引用标记，要删得显式打开
               fix_cjk:bool=True)->str:   #默认开CJK空格修正，中英混排的PDF几乎都需要
    """完整规范化流程。

    顺序很重要：先做 Unicode 规范化（后续正则才好写），再修连字符与空格，
    最后才切参考文献（切分依赖行结构，必须放在换行仍在的时候做）。
    """
    if not text:   #空串或'None'直接早退，返回空串而不是'None'，让调用方少一层判空
        return ""
    result=normalize_unicode(text)   #第一步：全角折半角、连字还原，后面所有正则都建立在干净字符上
    result=remove_invisible(result)   #第二步：清不可见字符，必须排在断词合并之前，否则零宽字符会顶在连字符旁边把正则挡掉
    result=normalize_punctuation(result)   #第三步：统一标点变体
    result=fix_hyphenation(result)   #第四步：合并跨行断词，此时换行还完整，正则可以正常看行尾
    if fix_cjk:   #开关为假就跳过分词友好化，适合纯英文或不需要分词的场景
        result=normalize_cjk_spacing(result)
    if strip_citations:   #默认关，打开才删正文里的'[1]'这种标记
        result=strip_inline_citations(result)
    result=collapse_whitespace(result)
    if strip_refs:   #默认开，摘要等场景由'clean_abstract'传'False'关掉
        result=strip_references(result)   #切参考文献必须放在压空白之后：切分要靠行结构，换行先被压没了就切不准
    result=collapse_whitespace(result)   #再压一次：上一步切完会留下多余空行，这里收尾
    return result


def clean_abstract(text:Optional[str])->Optional[str]:
    """规范化摘要（不切参考文献，摘要里不会有）。"""
    if not text:   #空串或'None'统一返回'None'：摘要字段的空值要区分「没有摘要」和「有但为空」
        return None
    result=clean_text(text,strip_refs=False)   #关掉切参考文献：摘要是单独抽出来的片段，里面不会带参考文献段，白扫一遍
    return result or None   #洗成空串的也归成'None'，避免把空字符串写进数据库当有效值


def detect_language(text:str,*,sample:int=3000)->str:
    """粗略判断语种。返回 ``zh`` / ``ja`` / ``en`` / ``unknown``。

    判据：假名信号达到阈值时判为日文，否则 CJK 字符占比 >15% 判为中文，
    拉丁字母占比 >40% 判为英文。
    不用 langdetect 之类的库——省一个依赖，且中英混排的学术文本本就难分。
    """
    if not text:   #空串或'None'归到'unknown'，调用方按未知处理即可
        return "unknown"
    sample_text=text[:sample]   #只取前若干字符：语种在开头几千字里就定型了，整篇统计纯属浪费
    total=len(sample_text)   #后续所有比例共用这一个分母
    if total==0:   #切片后可能为空（原文本全是空白字符），先拦一下防除零
        return "unknown"
    cjk=sum(1 for ch in sample_text if "\u4e00"<=ch<="\u9fff")   #只数基本汉字区：扩展区和假名不参与，避免日文被算成中文
    kana=sum(1 for ch in sample_text if "\u3040"<=ch<="\u30ff")
    latin=sum(1 for ch in sample_text if ch.isascii() and ch.isalpha())   #'isalpha'判字母、'isascii'排掉全角：全角字母已经被NFKC折过，这里再保险一层
    if kana>=2 and kana/max(1,kana+cjk)>0.05:
        return "ja"
    if cjk/total>0.15:   #中文这条先判：中英混排的论文中文占比不高也能过线，且此刻还没做分词
        return "zh"
    if latin/total>0.40:   #拉丁字母占比这条给英文收尾，英文论文里夹公式数字也不会掉到线下
        return "en"
    return "unknown"   #两条都不满足就返回未知，不硬猜


def estimate_tokens(text:str)->int:
    """粗估 token 数。

    经验公式：英文约 4 字符/token；中文约 1.5 字符/token（含标点）。
    用于切块与上下文预算控制，不需要精确——真要精确得引入 tokenizer。
    """
    if not text:   #空串或'None'返回0，调用方可以直接累加不用判空
        return 0
    cjk=sum(1 for ch in text if "\u4e00"<=ch<="\u9fff")   #和'detect_language'口径一致：只数基本汉字区
    other=len(text)-cjk   #剩下的全按西文算：标点、数字、空白都算进去，反正是粗估
    return int(cjk/1.5+other/4.0)+1   #中文按1.5字符一个算、西文按4个算，用'/1.5'当乘除换算；末尾'+1'保证非空文本结果至少是1，不会算出0去把预算除出无穷


__all__=[   #声明'from litagent.clean.normalize import *'时要导入哪些名字
    "normalize_unicode",
    "remove_invisible",
    "fix_hyphenation",
    "normalize_cjk_spacing",
    "normalize_punctuation",
    "collapse_whitespace",
    "find_references_start",
    "strip_references",
    "strip_inline_citations",
    "clean_text",
    "clean_abstract",
    "detect_language",
    "estimate_tokens",
]
