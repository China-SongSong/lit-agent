"""结构化总结和论断抽取使用的提示词。"""
from __future__ import annotations

from typing import Any, Dict, List

#注释续：版本变了才会重新调用模型
PROMPT_VERSION="summary-v1"

SUMMARY_SYSTEM="""你是一位严谨的学术文献分析助手，服务于正在撰写论文的研究者。

你的任务是把一篇论文读薄：提炼研究问题、方法、发现、贡献与局限，并摘出可以直接
支撑论证的关键论断。

硬性要求：
1. 只依据提供的论文原文作答。**原文没有提到的内容，一律留空字符串**，
   绝对不要根据标题或常识推测填充。
2. 全部输出使用简体中文；但专业术语首次出现时用「中文（English）」形式
   保留英文原文，避免翻译失真。
3. 不做评价性发挥，不写"这是一篇优秀的论文"之类的话，只陈述事实。
4. 输出必须是**一个合法的 JSON 对象**，不要包裹 markdown 代码块，
   不要添加任何解释性文字。"""

SUMMARY_USER_TEMPLATE="""请分析下面这篇论文，并按要求输出 JSON。

论文题名：{title}
发表年份：{year}
发表出处：{venue}

===== 论文正文开始 =====
{content}
===== 论文正文结束 =====

请输出如下结构的 JSON（所有字段都必须存在）：

{{
  "tldr": "一句话概括这篇论文做了什么（40 字以内）",
  "problem": "它要解决的研究问题是什么，为什么这个问题重要",
  "methods": "它用了什么方法/模型/实验设计。要具体到关键技术名称",
  "findings": "主要实验结果与结论。**尽量带上具体数字**（提升多少、在哪些数据集上）",
  "contributions": "这篇论文的学术贡献，分点陈述",
  "limitations": "作者自己承认的局限，或从实验设计中能看出的局限",
  "keywords": ["关键词1", "关键词2"],
  "claims": [
    {{
      "text": "一条可以直接写进论文、需要引用这篇文献来支撑的论断",
      "section": "该论断出自论文的哪一节（如 method / experiment）",
      "evidence": "支撑该论断的原文依据，一句话"
    }}
  ]
}}

说明：
- `claims` 是重点，请给 3~6 条。它们应当是可以被引用、能被原文证实的判断，
  而不是泛泛而谈（反例："本文提出了一个新方法"；正例："引入注意力机制后，
  在 YooChoose 数据集上 Recall@20 相比 GRU4Rec 提升约 12%"）。
- 原文中没有的信息，对应字段填空字符串 ""，不要编造。"""

CLAIMS_SYSTEM="""你是一位学术写作助手。你的任务是从论文片段中摘出**可以被引用**的论断。

一条合格的论断必须：
1. 是一个明确的、有信息量的判断（不是"本文研究了X"这种描述）
2. 有原文依据，且原文依据必须来自你收到的片段
3. 适合在他人论文里作为论据使用，且必须标注引用来源

输出必须是合法的 JSON，不要包裹代码块。"""

CLAIMS_USER_TEMPLATE="""下面是一篇论文的若干片段，请从中摘出 3~6 条可直接引用支撑论证的论断。

各片段已标注编号、章节与页码。

{segments}

请输出：
{{
  "claims": [
    {{
      "text": "论断内容（简洁、可独立成立）",
      "section": "来源章节",
      "page": 页码数字,
      "evidence": "原文中支撑该论断的原句（照抄，不要改写）",
      "chunk_index": 片段编号数字
    }}
  ]
}}

要求：只使用上面片段里出现过的信息。如果某条论断在片段中找不到原句支撑，就不要输出它。"""

def build_summary_messages(title:str,year:Any,venue:Any,
                           content:str,  #参数续行：`content` 是论文正文
                           max_chars:int=48000)->List[Dict[str,str]]:
    """构造结构化总结的消息列表。"""
    body=_truncate_keep_edges(content,max_chars)
    return[  #开始构造返回值
        {"role":"system","content":SUMMARY_SYSTEM},
        {"role":"user","content":SUMMARY_USER_TEMPLATE.format(
            title=title or "(未知)",
            year=year if year else "(未知)",  #年份用条件表达式兜底，取不到时显示未知
            venue=venue or "(未知)",
            content=body,
        )},
    ]  #return 的列表结束，函数返回两条消息

def build_claims_messages(segments:List[Dict[str,Any]])->List[Dict[str,str]]:
    """构造论断抽取的消息列表。"""
    lines:List[str]=[]
    for segment in segments:
        lines.append("【片段 %d】章节：%s ｜ 页码：%s"
                     %(segment.get("index",0),
                        segment.get("section") or "未知",  #章节取不到或为空时填未知
                        segment.get("page") if segment.get("page") else "未知"))
        lines.append(segment.get("text",""))
        lines.append("")
    return[  #开始返回结构
        {"role":"system","content":CLAIMS_SYSTEM},
        {"role":"user","content":CLAIMS_USER_TEMPLATE.format(
            segments="\n".join(lines))},
    ]

def _truncate_keep_edges(text:str,max_chars:int)->str:
    """保留头尾地截断长文本。"""
    if not text or len(text)<=max_chars:
        return text or ""
    half=max_chars//2
    head=text[:half]
    tail=text[-half:]
    return(head+"\n\n……（中间部分因长度限制省略）……\n\n"+tail)

__all__=[
    "PROMPT_VERSION",  #导出提示词版本号
    "SUMMARY_SYSTEM",
    "SUMMARY_USER_TEMPLATE",
    "CLAIMS_SYSTEM",
    "CLAIMS_USER_TEMPLATE",  #导出论断抽取的用户模板
    "build_summary_messages",
    "build_claims_messages",
]
