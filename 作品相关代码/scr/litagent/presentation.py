"""Web 界面的可测试展示辅助。"""
from __future__ import annotations

import html
import re


def readable_text_html(value:str,*,max_height_vh:int=68)->str:
    """把抽取正文整理成高对比、可滚动的只读阅读区。"""
    text=str(value or "").replace("\r\n","\n").replace("\r","\n")
    text=re.sub(r"[ \t\f\v]+"," ",text)
    text=re.sub(r"\n[ \t]+","\n",text)
    text=re.sub(r"[ \t]+\n","\n",text)
    text=re.sub(r"\n{3,}","\n\n",text).strip()
    blocks=[
        block.strip() for block in re.split(r"\n\s*\n+",text)
        if block.strip()
    ]
    paragraphs=[]
    for block in blocks:
        # PDF 抽取常把同一段拆成多行；中文行直接拼接，英文行保留一个空格。
        block=re.sub(
            r"(?<=[\u3400-\u4dbf\u4e00-\u9fff])[ \t]*\n[ \t]*"
            r"(?=[\u3400-\u4dbf\u4e00-\u9fff])",
            "",
            block,
        )
        block=re.sub(r"[ \t]*\n[ \t]*"," ",block).strip()
        paragraphs.append(
            '<p style="margin:0 0 1.05em 0;">%s</p>'
            %html.escape(block).replace("  ","&nbsp;&nbsp;")
        )
    if not paragraphs:
        paragraphs=['<p style="margin:0;">（无正文）</p>']
    return(
        '<div class="litagent-readable-text" style="'
        "max-height:%dvh;overflow-y:auto;padding:1.2rem 1.35rem;"
        "border:1px solid #cbd5e1;border-radius:0.8rem;"
        "background:#f8fafc;color:#172033;font-size:1rem;"
        "line-height:1.9;letter-spacing:0.015em;"
        'word-break:break-word;overflow-wrap:anywhere;">%s</div>'
        %(max_height_vh,"".join(paragraphs))
    )


__all__=["readable_text_html"]
