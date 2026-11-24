# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, os, sqlite3
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.llm.client import extract_json
from litagent.schema import Document, Author, title_similarity, normalize_title
from litagent.fetch.runner import FetchRunner, DEFAULT_TITLE_THRESHOLD
RES=[]
def rec(no, name, ok, detail=""):
    RES.append(dict(no=no, name=name, ok=bool(ok), detail=str(detail)[:300]))
    print("%-6s #%-2s %-42s %s" % ("PASS" if ok else "FAIL", no, name, str(detail)[:100]))

Q = chr(34); BS = chr(92); FENCE = chr(96)*3; NL = chr(10)
cases14 = [
 ("markdown 围栏", '前言 ' + FENCE + 'json' + NL + '{"a": 1}' + NL + FENCE + ' 后记', {"a":1}),
 ("前后废话", '好的，结果如下：{"a": 2} 希望有帮助', {"a":2}),
 ("嵌套括号", '{"a": {"b": [1,2,{"c":3}]}}', {"a":{"b":[1,2,{"c":3}]}}),
 ("字符串内含转义引号与括号", '{"a": "他说 ' + BS + Q + '括号{不是结构}' + BS + Q + ' 结束"}',
                          {"a": "他说 " + Q + "括号{不是结构}" + Q + " 结束"}),
]
for name, text, exp in cases14:
    got = extract_json(text)
    rec(14, "JSON 提取：%s" % name, got==exp, "得到 %r" % (got,))

rec(15, "残缺括号与无 JSON 返回 None",
    extract_json('{"a": 1') is None and extract_json('没有 JSON') is None)

s = get_settings()
TMP = str(_REPO_ROOT / '测试资产' / 'data' / 'dedupe_copy.db')
for p in (TMP, TMP+"-wal", TMP+"-shm"):
    try: os.remove(p)
    except Exception: pass
src = sqlite3.connect(str(s.db_path)); dst = sqlite3.connect(TMP); src.backup(dst); dst.close(); src.close()
store = Store(TMP); store.init_schema()
runner = FetchRunner(store, s)
def mk(did, title, doi=None, abstract=None, venue=None, source="openalex"):
    return Document(id=did, title=title, doi=doi, abstract=abstract, venue=venue,
                    source=source, authors=[Author(family="Test", given="A")], year=2026)
before = store.count_documents()
d1 = mk("t:a1", "A Test Paper on Fusion", "10.1234/ABC.2026.001", abstract="摘要一")
d2 = mk("t:a2", "A Test Paper on Fusion", "https://doi.org/10.1234/abc.2026.001", venue="Journal X", source="crossref")
rep = runner.ingest([d1, d2])
saved = store.get_document("t:a1", with_fulltext=False)
rec(16, "同 DOI 跨源去重且字段合并",
    store.count_documents()==before+1 and rep.merged==1
    and saved is not None and saved.abstract=="摘要一" and saved.venue=="Journal X",
    "新增=%d merged=%d abstract=%r venue=%r" % (store.count_documents()-before, rep.merged,
        (saved.abstract if saved else None), (saved.venue if saved else None)))
n1 = store.count_documents()
d3 = mk("t:b1", "Normalization  Test: A Case", None)
d4 = mk("t:b2", "normalization test:  a case", None)
rep2 = runner.ingest([d3, d4])
rec(17, "标题归一化判重（大小写/空白）", rep2.merged==1 and store.count_documents()==n1+1,
    "merged=%d 新增=%d" % (rep2.merged, store.count_documents()-n1))
base_title = "Deep Learning Approaches for Citation Network Analysis in Scholarly Papers"
cand = None
for suffix in ["", " Today", " 2024", " Revisited", " Extended", " in Practice", " and Beyond", " Methods"]:
    t = (base_title + suffix).strip()
    sim = title_similarity(normalize_title(t), normalize_title(base_title))
    if 0.85 <= sim < DEFAULT_TITLE_THRESHOLD and cand is None:
        cand = (t, sim)
print("  候选相似度样例：%s" % (("%.3f" % cand[1]) if cand else "未找到"))
rec(18, "相似度低于阈值时不去重", cand is not None and cand[1] < DEFAULT_TITLE_THRESHOLD,
    "相似度 %.3f < 阈值 %.2f" % (cand[1] if cand else -1, DEFAULT_TITLE_THRESHOLD))
n0 = store.count_documents()
d5 = mk("t:c1", "Batch Pending Paper Alpha", "10.5555/batch.001", abstract="x")
d6 = mk("t:c2", "Batch Pending Paper Alpha", "10.5555/batch.001", venue="V")
rep3 = runner.ingest([d5, d6])
rec(19, "批内同 DOI 被 pending 映射拦截", store.count_documents()==n0+1 and rep3.merged==1,
    "新增=%d merged=%d" % (store.count_documents()-n0, rep3.merged))
store.close()
for p in (TMP, TMP+"-wal", TMP+"-shm"):
    try: os.remove(p)
    except Exception: pass
fails=[x for x in RES if not x["ok"]]
print("\n== JSON 提取与去重：%d 项，失败 %d ==" % (len(RES), len(fails)))
for f in fails: print("  FAIL #%s %s %s" % (f["no"], f["name"], f["detail"][:140]))
