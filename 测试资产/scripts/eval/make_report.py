# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

CN = "微软雅黑"
OUT = str(_REPO_ROOT.parent / 'lit-agent 系统测试报告.docx')
NAVY = RGBColor(0x1F,0x3A,0x66); BLUE = RGBColor(0x2E,0x5C,0x8A)
GREY = RGBColor(0x66,0x66,0x66); RED = RGBColor(0xB3,0x2D,0x2D)
GREEN = RGBColor(0x1E,0x6B,0x3A); AMBER = RGBColor(0x8A,0x5A,0x10)

doc = Document()
sec = doc.sections[0]
sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
sec.left_margin = sec.right_margin = Cm(2.2)
sec.top_margin = sec.bottom_margin = Cm(2.0)
st = doc.styles["Normal"]; st.font.name = CN; st.font.size = Pt(10.5)
st.element.rPr.rFonts.set(qn("w:eastAsia"), CN)
st.paragraph_format.line_spacing = 1.3
st.paragraph_format.space_after = Pt(4)

def fmt(r, size=10.5, bold=False, color=None, mono=False):
    r.font.name = "Consolas" if mono else CN
    r.font.size = Pt(size); r.bold = bold
    r._element.rPr.rFonts.set(qn("w:eastAsia"), CN)
    if color: r.font.color.rgb = color

def P(t, size=10.5, bold=False, color=None, before=2, after=4, mono=False, indent=0, align=None):
    p = doc.add_paragraph()
    if align == "c": p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(before); p.paragraph_format.space_after = Pt(after)
    if indent: p.paragraph_format.left_indent = Cm(indent)
    fmt(p.add_run(t), size, bold, color, mono); return p

def H1(text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(15); p.paragraph_format.space_after = Pt(7)
    fmt(p.add_run(text), 13, True, NAVY)
    pPr = p._p.get_or_add_pPr(); bd = OxmlElement("w:pBdr"); bot = OxmlElement("w:bottom")
    bot.set(qn("w:val"),"single"); bot.set(qn("w:sz"),"8"); bot.set(qn("w:color"),"1F3A66")
    bd.append(bot); pPr.append(bd)

def H2(t): P(t, size=11, bold=True, color=BLUE, before=10, after=4)

def shade(cell, hexc):
    tcPr = cell._tc.get_or_add_tcPr(); shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), hexc); tcPr.append(shd)

def TBL(headers, rows, widths=None, colors=None):
    t = doc.add_table(rows=1, cols=len(headers)); t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i,h in enumerate(headers):
        c = t.rows[0].cells[i]; c.text = ""
        pr = c.paragraphs[0]; pr.paragraph_format.space_after = Pt(1)
        fmt(pr.add_run(h), 9, True, NAVY); shade(c, "E2E9F4")
    for ri,row in enumerate(rows):
        cells = t.add_row().cells
        for i,v in enumerate(row):
            cells[i].text = ""
            pr = cells[i].paragraphs[0]; pr.paragraph_format.space_after = Pt(1)
            col = None
            if colors and i == colors.get("col"):
                col = colors.get("map", {}).get(str(v))
            fmt(pr.add_run(str(v)), 9, False, col)
            if ri % 2 == 1: shade(cells[i], "F7F9FC")
    if widths:
        for i,w in enumerate(widths):
            for r in t.rows: r.cells[i].width = Cm(w)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)

COLOR_MAP = {"PASS": GREEN, "FAIL": RED, "PART": AMBER}

# ================= 封面 =================
P("lit-agent 系统测试报告", 20, True, NAVY, before=0, after=2, align="c")
P("被测对象：桌面版 lit_agent（作品相关代码\\scr）", 10.5, False, GREY, after=2, align="c")
P("测试时间：2026-10-29 21:58 — 22:15　　测试方式：自动化脚本 + 无头界面 + 命令行", 9.5, False, GREY, after=12, align="c")

# ================= 1 概述 =================
H1("一、测试概述")
P("本次对 lit-agent 做一次完整回归，覆盖环境、模块、数据库、检索、溯源、引文、总结、分块与索引、"
  "界面与 Agent 对话共十个方面。测试以只读调用为主；涉及写入的用例（删除、Agent 联网抓取）"
  "分别在数据库副本或测试后回滚，测试结束已核对数据回到测试前状态。")
P("测试结论：核心功能（溯源、检索、界面、数据库一致性）全部通过；共记录 8 条问题（高 2 条、中 2 条、低 4 条），"
  "其中 2 条对应 Issues.md 中已登记但未真正修复的条目。")

# ================= 2 环境 =================
H1("二、测试环境")
TBL(["项目", "实测值"],
    [["操作系统", "Windows 10（10.0.19045）"],
     ["Python", "3.14.7"],
     ["依赖版本", "requests 2.34.2 / jieba 0.42.1 / PyMuPDF 1.28.2 / streamlit 1.64.0"],
     ["数据库", "SQLite，index.db，11.4 MB"],
     ["语料规模", "49 篇文献（含全文 8 篇）、254 个片段、21710 条 posting、6321 个词条、47834 条指纹"],
     ["大模型", "deepseek-chat（OpenAI 兼容端点），已配置密钥"],
     ["环境自检", "doctor 无 FAIL；2 项 WARN：vendor 依赖目录不存在、Semantic Scholar 返回 429 限流"]],
    [3.4, 12.8])

# ================= 3 结果总览 =================
CASES = [
 ("环境与依赖", "Python 版本符合要求", "PASS", "3.14.7 ≥ 3.9"),
 ("环境与依赖", "四项必需依赖可导入", "PASS", "requests/jieba/PyMuPDF/streamlit"),
 ("环境与依赖", "数据源连通性", "PASS", "openalex/crossref/arxiv 均 HTTP 200；s2 429 限流"),
 ("环境与依赖", "LLM 已配置", "PASS", "密钥 35 字符"),
 ("环境与依赖", "doctor 自检无 FAIL", "PASS", "2 项 WARN 不阻断"),
 ("模块", "全部模块可导入", "PASS", "40 个模块"),
 ("模块", "入口文件可解析", "PASS", "app.py / cli.py"),
 ("数据库", "数据库存在且可连接", "PASS", "index.db"),
 ("数据库", "PRAGMA integrity_check", "PASS", "ok"),
 ("数据库", "数据表齐全", "PASS", "11 张表"),
 ("数据库", "计数非零", "PASS", "49/254/21710/47834/8"),
 ("数据库", "孤儿数据检查（5 类）", "PASS", "全部为 0"),
 ("数据库", "空文本片段", "PASS", "0 条"),
 ("数据库", "全文标记与正文长度一致性", "PASS", "has_fulltext=1 的 8 篇正文 19k–63k 字符；其余 72–4891，无错判/漏判"),
 ("数据库", "指纹 n 与配置一致", "PASS", "index=7，cfg=7"),
 ("索引", "每个片段都有倒排 posting", "PASS", "缺失 0 / 254"),
 ("索引", "每个片段都有指纹", "PASS", "缺失 0 / 254"),
 ("索引", "片段长度不超过配置上限", "PASS", "最大 1800 字符 = 上限 1800"),
 ("检索", "多组查询均返回结果", "PASS", "3 组各命中 5 条"),
 ("检索", "结果按分数降序", "PASS", "分数单调不增"),
 ("检索", "结果含文献对象", "PASS", "全部非空"),
 ("检索", "空查询不抛异常", "PASS", "返回空列表"),
 ("溯源", "逐字原文命中", "PASS", "level=local_exact，置信度 0.990，候选 1"),
 ("溯源", "置信度达到精确档", "PASS", "0.990 ≥ 0.85"),
 ("溯源", "返回出处文献与引文", "PASS", "Cui et al. 2025，DOI 10.51408/issi2025_147"),
 ("溯源", "微改动仍可命中且有区分度", "PASS", "local_fuzzy，0.867（原文 0.990）"),
 ("溯源", "无关文本判为未找到", "PASS", "level=none，置信度 0.000"),
 ("溯源", "空输入不抛异常", "PASS", "note=输入为空。"),
 ("溯源", "过短输入明确拒绝", "PASS", "2 个词元，提示补充完整句子"),
 ("溯源", "命令行 trace 端到端", "PASS", "99%，0.03 秒，输出上下文与 GB/T 7714 引文"),
 ("引文", "批量生成 GB/T 7714 引文", "PASS", "6 篇均正常"),
 ("引文", "作者名含 HTML 标签不进入引文", "FAIL", "输入 <i>Zhang</i> → 输出 <I>ZHANG</I>"),
 ("总结", "已有结构化总结可读取", "PASS", "8 篇"),
 ("总结", "结构化字段完整度", "FAIL", "8 篇中 2 篇完整、3 篇部分、3 篇六个字段全空（仅存 claims）"),
 ("总结", "缓存命中时无需 API Key", "FAIL", "清空密钥后调用仍抛『未配置LLM API Key』"),
 ("边界", "只读连接拒绝写入", "PASS", "写入被拒"),
 ("边界", "删除文献事务性", "PASS", "副本上 49→48，片段/倒排/指纹/总结/正文全部清零，无孤儿"),
 ("边界", "中文短句可被指纹命中（6 字）", "PASS", "合成中文语料：local_exact 0.990，找到"),
 ("边界", "中文短句可被指纹命中（5 字）", "PASS", "local_fuzzy 0.743，找到"),
 ("边界", "中文短句可被指纹命中（4 字）", "PART", "可召回（候选 1）但置信度 0.690 < 0.72，判为未找到"),
 ("边界", "增量索引一致性", "PASS", "无缺倒排或缺指纹的文献"),
 ("界面", "Streamlit 服务可启动", "PASS", "HTTP 200"),
 ("界面", "健康检查", "PASS", "/_stcore/health 返回 ok"),
 ("界面", "应用无异常运行", "PASS", "AppTest 无 exception"),
 ("界面", "页签与控件齐全", "PASS", "3 个页签、1 个文本域、5 个按钮"),
 ("界面", "溯源交互出结果", "PASS", "『找到出处（置信度 99%，级别 local_exact）』"),
 ("界面", "结果区展示置信度与章节", "PASS", "metric：置信度 99%、所在章节 introduction"),
 ("界面", "溯源诊断信息", "PASS", "220 字 / 30 词元，指纹 n=7，BM25 有旁证，0.01 秒"),
 ("Agent", "简单问答（语料统计）", "PASS", "2 轮 / 2.6 秒 / corpus_stats"),
 ("Agent", "溯源类问答", "PASS", "2 轮 / 3.4 秒 / trace_quote"),
 ("Agent", "检索类问答", "PASS", "8 轮 / 121.4 秒，调用 search_papers、fetch_online、format_citation，撞满轮数上限"),
]
from collections import OrderedDict
cat = OrderedDict()
for c, n, s, d in CASES:
    cat.setdefault(c, []).append((n, s, d))
npass = sum(1 for x in CASES if x[2]=="PASS")
nfail = sum(1 for x in CASES if x[2]=="FAIL")
npart = sum(1 for x in CASES if x[2]=="PART")
total = len(CASES)

H1("三、结果总览")
P("共执行 %d 项检查：通过 %d 项、失败 %d 项、部分通过 %d 项，通过率 %.1f%%。"
  % (total, npass, nfail, npart, 100.0*npass/total))
rows = []
for c, items in cat.items():
    p = sum(1 for x in items if x[1]=="PASS"); f = sum(1 for x in items if x[1]=="FAIL"); q = sum(1 for x in items if x[1]=="PART")
    rows.append([c, len(items), p, f, q])
rows.append(["合计", total, npass, nfail, npart])
TBL(["类别", "用例数", "通过", "失败", "部分"], rows, [5.0, 3.0, 3.0, 3.0, 3.0])

# ================= 4 分项 =================
H1("四、分项结果")
for c, items in cat.items():
    H2(c)
    TBL(["检查项", "结果", "实测证据"], [[n, s, d] for n, s, d in items],
        [6.4, 1.6, 9.0], colors={"col":1, "map":COLOR_MAP})

# ================= 5 缺陷 =================
H1("五、缺陷清单")
DEF = [
 ["D1", "高", "作者名中的 HTML 标签会进入引文",
  "构造 <i>Zhang</i> 的作者名，输出引文为 <I>ZHANG</I> J, WANG Z…；<sup> 同样保留。当前语料无含标签作者，属潜在缺陷。",
  "Issues.md #5，未修复"],
 ["D2", "高", "结构化总结命中缓存时仍强制要求 API Key",
  "把 llm_api_key 置空后对已缓存的文献调用 SummaryRunner.run()，仍在入口抛『未配置LLM API Key』；"
  "而 extractor 本身已支持缓存命中路径，是 runner 的提前校验挡住了。",
  "Issues.md #6，未修复"],
 ["D3", "中", "部分结构化总结的六个字段全空",
  "8 篇总结中 3 篇 tldr/problem/methods/findings/contributions/limitations 全为空，仅 claims_json 有内容，"
  "界面展示会缺少研究问题、方法、贡献等字段。",
  "数据质量"],
 ["D4", "中", "Agent 的 fetch_online 会直接写入生产语料库且无确认",
  "测试中询问 AlphaFold 相关文献，Agent 自动联网抓取 8 篇并入库，文献 49→57、片段 254→318，"
  "同时生成 raw/*.jsonl 与 pdfs/*.pdf。缺少二次确认或“仅预览不入库”模式。",
  "设计风险"],
 ["D5", "低", "4 字中文短句可召回但被判定为未找到",
  "合成中文语料上 4 字短句置信度 0.690，低于 found 阈值 0.72，界面显示未找到，"
  "而同样短句实际已正确召回。属阈值保守导致，不算错误但用户可感知。",
  "Issues.md #4 部分修复"],
 ["D6", "低", "doctor 列出 REST API 依赖但无对应实现",
  "config.py 检查 fastapi/uvicorn 并标注『REST API』，但代码库中不存在任何 API 服务。",
  "文档一致性"],
 ["D7", "低", "空输入的等级与“未找到”不可区分",
  "TraceResult.level 默认值即 none，空输入未单独设等级，只能靠 note 文本区分。",
  "可用性"],
 ["D8", "低", "复杂问题会撞满 8 轮上限且耗时过长",
  "检索类问题用满 8 轮、121.4 秒，其中含联网抓取。默认上限 8 轮对含联网的复合任务偏紧。",
  "性能/体验"],
]
TBL(["编号", "级别", "缺陷", "证据", "备注"], DEF, [1.2, 1.2, 4.6, 7.4, 3.2],
    colors={"col":1, "map":{"高":RED, "中":AMBER, "低":GREY}})

# ================= 6 数据影响 =================
H1("六、测试对数据的影响与回滚")
P("测试过程中有两处会产生写入：删除用例在数据库副本上执行；Agent 联网抓取直接写入生产库。"
  "后者已完整回滚，测试前后数据快照对比如下：")
TBL(["对象", "测试前", "测试中临时变化", "回滚后"],
    [["文献", "49 篇", "57 篇（+8 篇 AlphaFold）", "49 篇"],
     ["片段", "254 个", "318 个", "254 个"],
     ["精确指纹", "47834 条", "—", "47834 条"],
     ["raw/*.jsonl", "—", "新增 1 个", "已删除"],
     ["pdfs/*.pdf", "—", "新增 1 个", "已删除"],
     ["ingest_log", "14 条", "15 条", "14 条"],
     ["integrity_check", "ok", "—", "ok"]],
    [3.4, 3.6, 6.0, 3.4])
P("回滚后重新核对：孤儿数据 5 类全为 0，integrity_check 为 ok，与测试前完全一致。", 9.5, False, GREY)

# ================= 7 结论 =================
H1("七、结论与建议")
P("核心能力可用。", 10.5, True, GREEN)
P("溯源链路（指纹召回 → 跨片段拼接 → 置信度换算 → 五级判定 → 引文生成）在命令行与网页两端结果一致，"
  "逐字原文 99%、微改动 86.7%、无关文本正确判为未找到；数据库一致性与删除事务性均无问题；"
  "分块上限、增量索引、只读连接三项 Issues.md 条目经验证已修复。")
P("建议优先处理两项高优先级缺陷。", 10.5, True, RED)
P("D1 建议在引文格式化入口对作者字段做一次标签剥除（正则去掉 <...> 后再做大小写转换），"
  "否则一旦接入含 HTML 的元数据源，引文会直接带上标签，影响可直接引用的承诺。", indent=0.4)
P("D2 建议把 require_llm() 从 SummaryRunner.run() 入口下沉到真正调用模型之前，"
  "让纯缓存命中的批量导出在无密钥环境下也能工作。", indent=0.4)
P("另外建议：", 10.5, True, BLUE, before=8)
P("（1）重新生成 D3 涉及的 3 篇总结，或在 UI 上对字段缺失的总结明确标注“仅含论断”。", indent=0.4)
P("（2）为 fetch_online 增加“先预览后入库”的开关，避免演示时意外改动语料。", indent=0.4)
P("（3）把 found 阈值与中文短句的置信度映射分开考虑：短句的可信度不应完全由覆盖率线性换算。", indent=0.4)

# ================= 8 附录 =================
H1("八、附录：测试脚本")
TBL(["脚本", "用途"],
    [["stage1.py", "模块导入、数据库完整性与孤儿检查"],
     ["stage2.py", "检索、溯源、引文功能测试"],
     ["stage3.py", "总结、分块、只读、删除测试"],
     ["stage4_ui.py / probe_ui.py", "无头运行 Streamlit 界面并检查控件与结果"],
     ["stage5_agent.py", "Agent 对话三类问题"],
     ["stage6_zh.py", "合成中文语料验证短句指纹命中"],
     ["rollback.py / clean_log.py", "回滚 Agent 抓取产生的数据"]],
    [5.6, 10.6])
P("脚本位于仓库的 测试资产/data 目录，全部只读调用项目模块，未修改 lit_agent 中的任何源码。",
  9.5, False, GREY)

doc.save(OUT)
print("SAVED", OUT)
print("用例总数 %d：通过 %d / 失败 %d / 部分 %d" % (total, npass, nfail, npart))
