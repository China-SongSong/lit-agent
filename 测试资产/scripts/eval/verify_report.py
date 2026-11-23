# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

from docx import Document
import re, os
p = str(_REPO_ROOT.parent / 'lit-agent 系统测试报告.docx')
d = Document(p)
paras = [x.text for x in d.paragraphs]
allt = "\n".join(paras) + "\n" + "\n".join(" | ".join(c.text for c in r.cells) for t in d.tables for r in t.rows)
print("大小 KB:", round(os.path.getsize(p)/1024,1))
print("段落:", len(paras), " 表格:", len(d.tables), " 字符:", len(allt))
print("unicode 泄漏:", len(re.findall(r"\\u[0-9a-fA-F]{4}", allt)))
print("\n=== 标题结构 ===")
for x in paras:
    if re.match(r"^[一二三四五六七八]、", x) or re.match(r"^(环境与依赖|模块|数据库|索引|检索|溯源|引文|总结|边界|界面|Agent)$", x.strip()):
        print("  ", x[:60])
print("\n=== 表格 ===")
for i, t in enumerate(d.tables, 1):
    print("  表%d: %d行 x %d列 | %s" % (i, len(t.rows), len(t.columns),
          " | ".join(c.text for c in t.rows[0].cells)[:50]))
print("\n=== 前 8 段 ===")
print("\n".join(x for x in paras[:8] if x.strip()))
