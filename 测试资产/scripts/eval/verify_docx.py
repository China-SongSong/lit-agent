# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

from docx import Document
import re, os
p = str(_REPO_ROOT.parent / 'lit-agent 系统测试报告.docx')
md = open(str(_REPO_ROOT / 'Issues.md'), encoding="utf-8").read()
d = Document(p)
paras = [x.text for x in d.paragraphs if x.text.strip()]
txt = "\n".join(paras)
print("大小 KB:", round(os.path.getsize(p)/1024,1))
print("段落数:", len(paras))
print("正文字符:", len(txt.replace("\n","")))
print("原 md 字符（去空行）:", len("".join(l.strip() for l in md.splitlines() if l.strip())))
print("unicode 泄漏:", len(re.findall(r"\\u[0-9a-fA-F]{4}", txt)))
print("\n=== 一级标题 ===")
for x in paras:
    if re.match(r"^[一二三四五六七八]、", x): print("  " + x)
print("\n=== 二级标题 ===")
for x in paras:
    if re.match(r"^（[一二三四五六七八]）", x): print("  " + x)
print("\n=== 关键内容抽查 ===")
for kw in ["OnlineAccessDenied", "0.68", "0.97", "147.65", "58 倍", "过拟合", "决策", "104"]:
    print("  %-22s %s" % (kw, "有" if kw in txt else "缺"))
print("\n=== 开头 5 段 ===")
for x in paras[:5]: print("  " + x[:70])
