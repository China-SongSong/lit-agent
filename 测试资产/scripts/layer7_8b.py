# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[2]

import sys, json
sys.path.insert(0, str(_REPO_ROOT / '测试资产' / 'scripts'))
from harness import Case, DATA, GOLDEN
c = Case()
from litagent.cite.gbt7714 import format_gbt7714, format_bibtex, format_ris
from litagent.cite.guji import format_history_note, format_gbt7714_guji, _cn_numeral_to_int, _circled
from litagent.llm.client import extract_json, LLMClient, LLMError, LLMUsage
from litagent.config import build_settings

journal = GOLDEN["journal_en"]["doc"]
c.add("C1", "GB/T 期刊[J] 金样例", format_gbt7714(journal)==GOLDEN["journal_en"]["gbt"], format_gbt7714(journal)[:70])
mono = {"id":"m1","title":"文献引文溯源系统设计","authors":[{"family":"李","given":"四"}],
        "year":2024,"publisher":"科学出版社","publisher_place":"北京","venue_type":"book","language":"zh"}
c.add("C2", "GB/T 专著[M]", "[M]" in format_gbt7714(mono), format_gbt7714(mono)[:60])
conf = {"id":"c1","title":"Fingerprint Index for Provenance","authors":[{"family":"Doe","given":"Jane"}],
        "year":2023,"venue":"Proceedings of the Test Conference","venue_type":"conference","pages":"10-20","language":"en"}
c.add("C3", "GB/T 论文集[C]", "[C]" in format_gbt7714(conf), format_gbt7714(conf)[:60])
web = {"id":"w1","title":"lit-agent 项目主页","authors":[{"family":"张","given":"三"}],
       "year":2025,"url":"https://example.test/lit","access_date":"2025-11-20","venue_type":"web","language":"zh"}
c.add("C4", "GB/T 电子资源[EB/OL]", "[EB/OL]" in format_gbt7714(web), format_gbt7714(web)[:60])
zh3 = {"id":"z3","title":"多作者测试","authors":[{"family":"张","given":"三"},{"family":"李","given":"四"},{"family":"王","given":"五"}],
       "year":2024,"venue":"测试学报","venue_type":"journal","volume":"1","issue":"1","pages":"1-9","language":"zh"}
zh4 = {"id":"z4","title":"四作者","authors":[{"family":"张","given":"三"},{"family":"李","given":"四"},
        {"family":"王","given":"五"},{"family":"赵","given":"六"}],
        "year":2024,"venue":"测试学报","venue_type":"journal","volume":"1","issue":"1","pages":"1-9","language":"zh"}
c.add("C5", "四名以上作者省略为等", "等" in format_gbt7714(zh4), format_gbt7714(zh4)[:60])
c.add("C5b", "三名作者不省略", "等" not in format_gbt7714(zh3), format_gbt7714(zh3)[:52])
bt = format_bibtex(journal)
c.add("C6", "BibTeX 含 key 与字段", "@" in bt and "title" in bt.lower(), bt.split(chr(10))[0][:50])
ris = format_ris(journal)
c.add("C7", "RIS 含类型/作者/题名/年份", all(t in ris for t in ("TY  -","AU  -")) and ("TI  -" in ris or "T1  -" in ris)
      and ("PY  -" in ris or "Y1  -" in ris), ris.splitlines()[:3])
print("   观察：RIS 使用 TI/PY 而非 T1/Y1")

g = GOLDEN["guji"]["doc"]
c.add("C8", "古籍首次引证金样例", format_history_note(g, page="295", first=True, index_style="circled")==GOLDEN["guji"]["first"],
      format_history_note(g, page="295", first=True, index_style="circled")[:40])
c.add("C9", "古籍再次引证省略版", format_history_note(g, page="295", first=False, index_style="circled")==GOLDEN["guji"]["repeat"],
      format_history_note(g, page="295", first=False, index_style="circled")[:40])
c.add("C10", "圈码 ①–⑳ 与超 20 翻段",
      _circled(1)=="①" and _circled(20)=="⑳" and _circled(21)=="㉑" and _circled(50)=="㊿",
      "%s/%s/%s/%s" % (_circled(1),_circled(20),_circled(21),_circled(50)))
c.add("C11", "卷次中文数字转换", _cn_numeral_to_int("七")==7 and _cn_numeral_to_int("十二")==12,
      "%s/%s" % (_cn_numeral_to_int("七"), _cn_numeral_to_int("十二")))
c.add("C12", "古籍 GB/T 体例可用", "[M]" in format_gbt7714_guji(g), format_gbt7714_guji(g)[:50])

FENCE = chr(96)*3
matrix = [
 ("围栏", FENCE+"json"+chr(10)+'{"a":1}'+chr(10)+FENCE, {"a":1}),
 ("数组", "[1,2,3]", [1,2,3]),
 ("废话前缀", '结果如下：{"a":2}', {"a":2}),
 ("嵌套", '{"a":{"b":[1,{"c":2}]}}', {"a":{"b":[1,{"c":2}]}}),
 ("字符串内括号", '{"a":"x{y}z"}', {"a":"x{y}z"}),
 ("残缺", '{"a":1', None),
 ("纯文本", "no json", None),
]
bad = [n for n,t,e in matrix if extract_json(t)!=e]
c.add("L1", "extract_json 矩阵 7 例", not bad, bad)

import requests
s = build_settings()
class R:
    def __init__(self, code):
        self.status_code=code; self.headers={}
        body={"choices":[{"message":{"content":'{"a":1}'},"finish_reason":"stop"}],
              "usage":{"prompt_tokens":5,"completion_tokens":2}}
        self.content=json.dumps(body).encode(); self.text=json.dumps(body)
        self._body=body
    def json(self): return self._body
    def close(self): pass
    def raise_for_status(self): pass
codes = iter([429, 500, 200])
orig = requests.Session.request
requests.Session.request = lambda self, method, url, **kw: R(next(codes))
try:
    cl = LLMClient(s)
    ok_retry = True
    try: cl.chat_json([{"role":"user","content":"hi"}], retries=3)
    except Exception: ok_retry = False
finally:
    requests.Session.request = orig
c.add("L2", "429/5xx 退避后成功", ok_retry, "")

codes2 = iter([401])
requests.Session.request = lambda self, method, url, **kw: R(next(codes2))
try:
    cl = LLMClient(s)
    try:
        cl.chat_json([{"role":"user","content":"hi"}], retries=3); ok_401=False; msg="未抛错"
    except LLMError as e:
        ok_401=True; msg=str(e)[:50]
finally:
    requests.Session.request = orig
c.add("L3", "401 直接抛不重试", ok_401, msg)

u = LLMUsage(); u.add(LLMUsage(prompt_tokens=10, completion_tokens=5, calls=1))
u.add(LLMUsage(prompt_tokens=3, completion_tokens=2, calls=1))
c.add("L4", "用量累计", u.prompt_tokens==13 and u.completion_tokens==7 and u.calls==2,
      "%d/%d/%d" % (u.prompt_tokens, u.completion_tokens, u.calls))

res = c.report("第7层 cite + 第8层 LLM")
json.dump(res, open(str(DATA/"layer7_8.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
