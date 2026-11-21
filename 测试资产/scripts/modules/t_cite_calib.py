# -*- coding: utf-8 -*-
import sys, subprocess, json
sys.path.insert(0, "C:/Users/Lenovo/Desktop/lit_agent/作品相关代码/scr")
from litagent.cite.gbt7714 import format_gbt7714
from litagent.cite.guji import format_history_note, _circled
from litagent.schema import normalize_doi, title_similarity, normalize_title
RES=[]
def rec(no, name, ok, detail=""):
    RES.append(dict(no=no, name=name, ok=bool(ok), detail=str(detail)[:300]))
    print("%-6s #%-2s %-46s %s" % ("PASS" if ok else "FAIL", no, name, str(detail)[:90]))

# 20 GB/T 与古籍金样例
en = {"id":"d1","title":"Understanding Thermal Runaway in Lithium-Ion Batteries",
      "authors":[{"family":"Liu","given":"Chenchen"},{"family":"Dai","given":"Hai"},
                 {"family":"Wang","given":"Danyang"},{"family":"Ren","given":"Xiaobo"}],
      "year":2024,"venue":"Journal of The Electrochemical Society","venue_type":"journal",
      "volume":"171","issue":"12","pages":"120527","language":"en"}
GOLD_EN = "LIU C, DAI H, WANG D, et al. Understanding Thermal Runaway in Lithium-Ion Batteries[J]. Journal of The Electrochemical Society, 2024, 171(12): 120527."
rec("20.1", "GB/T 期刊金样例（作者超三人省略）", format_gbt7714(en)==GOLD_EN, format_gbt7714(en)[:80])

zh = {"id":"d2","title":"图神经网络推荐中的元学习冷启动方法",
      "authors":[{"family":"张","given":"三"},{"family":"李","given":"四"}],
      "year":2025,"venue":"计算机学报","venue_type":"journal","volume":"48","issue":"3","pages":"1-18","language":"zh"}
GOLD_ZH = "张三, 李四. 图神经网络推荐中的元学习冷启动方法[J]. 计算机学报, 2025, 48(3): 1-18."
rec("20.2", "GB/T 中文期刊金样例", format_gbt7714(zh)==GOLD_ZH, format_gbt7714(zh)[:80])

guji = {"id":"g1","book_title":"史记","chapter_title":"项羽本纪","chapter_index":"7",
        "authors":[{"family":"司马迁","given":""}],"year":"1959","publisher":"中华书局",
        "publisher_place":"北京","edition":"点校本","language":"zh","extra":{"collator":"点校者"}}
GOLD_FIRST = "《史记》卷7《项羽本纪》，点校者，北京：中华书局，点校本，第295页。"
GOLD_REPEAT = "《史记》卷7《项羽本纪》，点校者，第295页。"
rec("20.3", "古籍首次引证金样例", format_history_note(guji, page="295", first=True, index_style="circled")==GOLD_FIRST,
    format_history_note(guji, page="295", first=True, index_style="circled")[:70])
rec("20.4", "古籍再次引证省略出版项", format_history_note(guji, page="295", first=False, index_style="circled")==GOLD_REPEAT,
    format_history_note(guji, page="295", first=False, index_style="circled")[:70])
rec("20.5", "圈码超 20 正确映射", _circled(20)=="⑳" and _circled(21)=="㉑" and _circled(36)=="㊱" and _circled(51)=="[51]",
    "%s %s %s %s" % (_circled(20), _circled(21), _circled(36), _circled(51)))

# 21 归一化边界
cases = [
 ("大写与前缀", normalize_doi("https://doi.org/10.1000/ABC")=="10.1000/abc"),
 ("doi: 前缀", normalize_doi("doi:10.1000/XYZ")=="10.1000/xyz"),
 ("空值与空白", normalize_doi(None) is None and normalize_doi("   ") in (None,"")),
 ("去掉尾斜杠", normalize_doi("10.1000/ABC/")=="10.1000/abc"),
]
rec("21.1", "normalize_doi 边界", all(v for _,v in cases), [n for n,v in cases if not v])

rec("21.2", "title_similarity 自身为 1", abs(title_similarity("A Study of Things","A Study of Things")-1.0)<1e-9,
    title_similarity("A Study of Things","A Study of Things"))
rec("21.3", "title_similarity 空值为 0", title_similarity("","")==0.0 and title_similarity(None,"x")==0.0,
    "%s / %s" % (title_similarity("",""), title_similarity(None,"x")))
a, b = "Deep Learning for Citation Analysis", "Deep Learning for Citation Studies"
s1, s2 = title_similarity(a,b), title_similarity(b,a)
rec("21.4", "title_similarity 对称且低于阈值", abs(s1-s2)<1e-9 and 0<s1<0.92, "%.3f / %.3f" % (s1,s2))

# 22 校准：精确率不低于基线
out = subprocess.run([sys.executable,"-B","-m","litagent.evaluation.calibration",
                      "--corpus","tests/fixtures/calibration_corpus_v2.jsonl",
                      "--queries","tests/fixtures/calibration_queries_v3.jsonl","--format","json"],
                     cwd="C:/Users/Lenovo/Desktop/lit_agent/作品相关代码/scr",
                     capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
try:
    data = json.loads(out.stdout)
    rows = data.get("thresholds") or []
    r72 = next((x for x in rows if abs(float(x["threshold"])-0.72)<1e-9), None)
    prec = float(r72["precision"]) if r72 else -1
    rec("22", "阈值扫描 precision 不低于基线", prec >= 0.98,
        "0.72 处 precision=%.3f（策略下限 0.98）" % prec)
except Exception as e:
    rec("22", "阈值扫描 precision 不低于基线", False, "%s; stdout=%r" % (e, out.stdout[:80]))

fails=[x for x in RES if not x["ok"]]
print("\n== 引用与归一化、校准：%d 项，失败 %d ==" % (len(RES), len(fails)))
for f in fails: print("  FAIL #%s %s %s" % (f["no"], f["name"], f["detail"][:140]))
