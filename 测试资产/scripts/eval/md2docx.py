# -*- coding: utf-8 -*-
import re, os
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.enum.section import WD_SECTION
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

SRC = r"C:\Users\Lenovo\Desktop\lit_agent\Issues.md"
OUT = r"C:\Users\Lenovo\Desktop\lit-agent 系统测试报告.docx"
CN = "微软雅黑"
NAVY = RGBColor(0x1F,0x3A,0x66); BLUE = RGBColor(0x2E,0x5C,0x8A); GREY = RGBColor(0x66,0x66,0x66)

raw = open(SRC, encoding="utf-8").read().splitlines()
lines = [l.rstrip() for l in raw]

# ---- 解析 ----
title = ""
meta, sections = [], []
cur_sec = None; cur_sub = None
for l in lines:
    s = l.strip()
    if not s: continue
    if not title:
        title = s; continue
    if re.match(r"^[一二三四五六七八九十]、", s):
        cur_sec = {"h": s, "body": [], "subs": []}; cur_sub = None
        sections.append(cur_sec); continue
    if re.match(r"^（[一二三四五六七八九十]）", s):
        cur_sub = {"h": s, "body": []}
        cur_sec["subs"].append(cur_sub); continue
    if cur_sec is None:
        meta.append(s)
    elif cur_sub is not None:
        cur_sub["body"].append(s)
    else:
        cur_sec["body"].append(s)

print("标题:", title)
print("头部信息 %d 行" % len(meta))
print("章节 %d 个:" % len(sections))
for s in sections:
    print("  %-22s 正文%d段 子节%d" % (s["h"], len(s["body"]), len(s["subs"])))

# ---- 生成 ----
doc = Document()
sec = doc.sections[0]
sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
sec.left_margin = sec.right_margin = Cm(2.3)
sec.top_margin = sec.bottom_margin = Cm(2.1)

st = doc.styles["Normal"]; st.font.name = CN; st.font.size = Pt(10.5)
st.element.rPr.rFonts.set(qn("w:eastAsia"), CN)
st.paragraph_format.line_spacing = 1.5
st.paragraph_format.space_after = Pt(6)

def fmt(r, size=10.5, bold=False, color=None):
    r.font.name = CN; r.font.size = Pt(size); r.bold = bold
    r._element.rPr.rFonts.set(qn("w:eastAsia"), CN)
    if color: r.font.color.rgb = color

# 标题
p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_before = Pt(10); p.paragraph_format.space_after = Pt(10)
fmt(p.add_run(title), 20, True, NAVY)

# 头部信息
for m in meta:
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(2); p.paragraph_format.line_spacing = 1.3
    fmt(p.add_run(m), 9.5, False, GREY)

# 分隔
p = doc.add_paragraph(); p.paragraph_format.space_after = Pt(2)
pPr = p._p.get_or_add_pPr(); bd = OxmlElement("w:pBdr"); bot = OxmlElement("w:bottom")
bot.set(qn("w:val"),"single"); bot.set(qn("w:sz"),"12"); bot.set(qn("w:color"),"1F3A66")
bd.append(bot); pPr.append(bd)

def body(text):
    p = doc.add_paragraph()
    p.paragraph_format.first_line_indent = Cm(0.74)   # 首行缩进两字符
    p.paragraph_format.space_after = Pt(6)
    fmt(p.add_run(text), 10.5)

for sec_item in sections:
    # 一级标题
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(16); p.paragraph_format.space_after = Pt(8)
    p.paragraph_format.keep_with_next = True
    fmt(p.add_run(sec_item["h"]), 14, True, NAVY)
    pPr = p._p.get_or_add_pPr(); bd = OxmlElement("w:pBdr"); bot = OxmlElement("w:bottom")
    bot.set(qn("w:val"),"single"); bot.set(qn("w:sz"),"8"); bot.set(qn("w:color"),"1F3A66")
    bd.append(bot); pPr.append(bd)
    for t in sec_item["body"]:
        body(t)
    for sub in sec_item["subs"]:
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(10); p.paragraph_format.space_after = Pt(4)
        p.paragraph_format.keep_with_next = True
        fmt(p.add_run(sub["h"]), 11.5, True, BLUE)
        for t in sub["body"]:
            body(t)

# 页脚页码
footer = doc.sections[0].footer
fp = footer.paragraphs[0]; fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = fp.add_run(); fmt(run, 9, False, GREY)
fld = OxmlElement("w:fldSimple"); fld.set(qn("w:instr"), "PAGE")
r = OxmlElement("w:r"); t = OxmlElement("w:t"); t.text = "1"; r.append(t); fld.append(r)
fp._p.append(fld)

doc.save(OUT)
print("\nSAVED:", OUT, "%.1f KB" % (os.path.getsize(OUT)/1024))
