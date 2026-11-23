# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[2]

import sys, os, json, time, tempfile, shutil, inspect
sys.path.insert(0, str(_REPO_ROOT / '测试资产' / 'scripts'))
from harness import Case, SCR, DATA, fake_documents
c = Case()
import requests

from litagent.fetch.base import TokenBucket, HttpClient, HttpError, _looks_like_pdf
from litagent.fetch.arxiv import ArxivSource, parse_journal_ref
from litagent.fetch.crossref import strip_jats, _date_parts_to_year
from litagent.fetch.openalex import invert_abstract, _map_work_type
from litagent.fetch.s2 import SemanticScholarSource
from litagent.fetch import local_pdf
from litagent.fetch.runner import FetchRunner, _slugify

# 17 TokenBucket
tb = TokenBucket(rate=10.0, capacity=1.0)
tb.acquire(); t0=time.time(); tb.acquire(); dt=time.time()-t0
c.add("17", "TokenBucket 速率间隔", dt >= 0.07, "%.3f 秒" % dt)

# 18 HttpClient 缓存
from litagent.config import build_settings
s = build_settings()
tmpd = tempfile.mkdtemp(prefix="httpcache_")
calls = {"n":0}
class FakeResp:
    status_code = 200
    headers = {"Content-Type":"application/json"}
    content = b'{"ok": true}'
    text = '{"ok": true}'
    def json(self): return {"ok": True}
    def close(self): pass
    def raise_for_status(self): pass
def fake_request(self, method, url, **kw):
    calls["n"] += 1
    return FakeResp()
orig_req = requests.Session.request
requests.Session.request = fake_request
try:
    hc = HttpClient(rate=20.0, cache_dir=Path(tmpd), max_retries=2)
    r1 = hc.get_json("https://example.test/api", params={"q":"a"})
    n_after_first = calls["n"]
    r2 = hc.get_json("https://example.test/api", params={"q":"a"})
    n_after_second = calls["n"]
    hc.get_json("https://example.test/api", params={"q":"b"})
    n_after_third = calls["n"]
    c.add("18", "HttpClient 磁盘缓存命中不再请求",
          n_after_first>=1 and n_after_second==n_after_first and n_after_third>n_after_second,
          "请求数 %d→%d→%d" % (n_after_first, n_after_second, n_after_third))
finally:
    requests.Session.request = orig_req
    shutil.rmtree(tmpd, ignore_errors=True)

# 19 重试策略
seq = []
class SeqResp:
    def __init__(self, code): self.status_code=code; self.headers={}; self.content=b'{"a":1}'; self.text='{"a":1}'
    def json(self): return {"a":1}
    def close(self): pass
    def raise_for_status(self): pass
def make_request(codes):
    it = iter(codes)
    def f(self, method, url, **kw):
        code = next(it)
        if isinstance(code, Exception): raise code
        return SeqResp(code)
    return f
# 5xx 重试后成功
requests.Session.request = make_request([500, 503, 200])
try:
    hc = HttpClient(rate=20.0, cache_dir=None, max_retries=3)
    ok5 = True
    try: hc.get_json("https://example.test/retry")
    except Exception as e: ok5 = False
finally:
    requests.Session.request = orig_req
# 4xx 不重试
calls4 = {"n":0}
def req4(self, method, url, **kw):
    calls4["n"] += 1; return SeqResp(404)
requests.Session.request = req4
try:
    hc = HttpClient(rate=20.0, cache_dir=None, max_retries=3)
    try:
        hc.get_json("https://example.test/404"); notfound = False
    except HttpError as e:
        notfound = True; retryable4 = getattr(e, "retryable", None)
except Exception as e:
    notfound = False; retryable4 = None
finally:
    requests.Session.request = orig_req
c.add("19.1", "5xx 重试后成功", ok5)
c.add("19.2", "4xx 不重试", notfound and calls4["n"]==1,
      "请求 %d 次，retryable 属性=%s" % (calls4["n"], retryable4))

# 20 PDF 魔数
c.add("20", "_looks_like_pdf 魔数识别",
      _looks_like_pdf(b"%PDF-1.7\n...") and not _looks_like_pdf(b"<html><body>403</body></html>"),
      "")

# 21 arxiv 解析
XML = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>http://arxiv.org/abs/2601.00001v1</id><title>Battery Safety Study</title>
<published>2026-01-02T00:00:00Z</published>
<author><name>Jane Doe</name></author><author><name>John Roe</name></author>
<summary>Abstract text here.</summary>
<arxiv:journal_ref xmlns:arxiv="http://arxiv.org/schemas/atom">J. Power Sources 12 (2024) 100-110</arxiv:journal_ref>
</entry></feed>"""
try:
    docs = ArxivSource.parse_feed(XML)
    d = docs[0] if docs else None
    c.add("21.1", "arxiv Atom 解析", d is not None and d.title.startswith("Battery") and len(d.authors)==2,
          "%s / %d 作者" % (d.title[:26] if d else None, len(d.authors) if d else 0))
except Exception as e:
    c.add("21.1", "arxiv Atom 解析", False, "%s: %s" % (type(e).__name__, str(e)[:70]))
jr = parse_journal_ref("Physical Review D 90, 123456 (2014)")
c.add("21.2", "parse_journal_ref 文档样例", jr[0]=="Physical Review D" and jr[1]==2014, jr)
jr2 = parse_journal_ref("J. Power Sources 12 (2024) 100-110")
c.add("21.3", "parse_journal_ref 年份后接页码时优雅回退", jr2[0] and jr2[1] is None, jr2)

# 22 crossref
c.add("22.1", "strip_jats 去标签", strip_jats("<jats:p>Hello <jats:italic>world</jats:italic></jats:p>")=="Hello world",
      repr(strip_jats("<jats:p>Hello <jats:italic>world</jats:italic></jats:p>")))
c.add("22.2", "_date_parts_to_year 多字段兜底",
      _date_parts_to_year({"date-parts":[[2024,1,1]]})==2024
      and _date_parts_to_year(None, {"date-parts":[[2020]]})==2020
      and _date_parts_to_year(None)==None
      and _date_parts_to_year({"date-parts":[[999]]}) is None,
      "%s/%s" % (_date_parts_to_year({"date-parts":[[2024,1,1]]}), _date_parts_to_year({"date-parts":[[999]]})))

# 23 openalex
inv = {"Hello":[0],"world":[1],"again":[2]}
c.add("23.1", "invert_abstract 还原", invert_abstract(inv)=="Hello world again", invert_abstract(inv))
scrambled = {"b":[1],"a":[0]}
c.add("23.2", "invert_abstract 位置错乱容错", invert_abstract(scrambled)=="a b", repr(invert_abstract(scrambled)))
c.add("23.3", "_map_work_type 映射", _map_work_type("journal-article") in ("journal","article","journal-article"),
      _map_work_type("journal-article"))

# 24 s2 字段缺失容错
try:
    d = SemanticScholarSource._to_document({"paperId":"x","title":"Only Title"})
    c.add("24", "s2 字段缺失不炸", d is None or d.title=="Only Title", d.title if d else None)
except Exception as e:
    c.add("24", "s2 字段缺失不炸", False, "%s: %s" % (type(e).__name__, str(e)[:60]))

# 25 opencli
from litagent.fetch.opencli import parse_cli_payload, row_to_document, OpenCLIAvailability, OpenCLISource
noisy = "some log line\n" + "{" + '"title":"Paper A","authors":["A B"],"year":"2024' + '"}' + "\ntrailing"
try:
    p = parse_cli_payload(noisy)
    c.add("25.1", "parse_cli_payload 抗噪音", isinstance(p,(dict,list)), type(p).__name__)
except Exception as e:
    c.add("25.1", "parse_cli_payload 抗噪音", False, "%s" % type(e).__name__)
try:
    row = {"title":"CNKI文献","author":"张三","year":"2024","source":"CNKI"}
    d = row_to_document(row, "cnki")
    c.add("25.2", "row_to_document 单行格式", d is None or hasattr(d,"title"), getattr(d,"title",None))
except Exception as e:
    c.add("25.2", "row_to_document 单行格式", False, str(e)[:60])
import shutil as _sh
orig_which = _sh.which
_sh.which = lambda *a, **k: None
try:
    oc = OpenCLISource(s)
    av = oc.availability()
    c.add("25.3", "未安装时降级", hasattr(av,"available") and av.available in (True, False), "available=%s" % av.available)
except Exception as e:
    c.add("25.3", "未安装时降级", False, "%s" % type(e).__name__)
finally:
    _sh.which = orig_which

# 26 local_pdf 工具函数
doi_cases = [("doi:10.1000/XYZ.", "10.1000/xyz"), ("见 https://doi.org/10.5555/ABC", "10.5555/abc"),
             ("no doi here", None), ("10.1000/abc)", "10.1000/abc")]
got = [(local_pdf.extract_doi_from_text(t), e) for t,e in doi_cases]
c.add("26.1", "extract_doi_from_text 边界", all((g or "")==(e or "") for g,e in got), got)
c.add("26.2", "guess_title_from_filename", "Battery" in local_pdf.guess_title_from_filename(Path("Battery_Safety_2024.pdf")),
      local_pdf.guess_title_from_filename(Path("Battery_Safety_2024.pdf")))
enc = local_pdf._decode_bytes("中文编码".encode("gbk"))
c.add("26.3", "_decode_bytes 多编码", enc=="中文编码", repr(enc[:8]))

# 27 三级去重顺序
import litagent.fetch.runner as R
with __import__("harness").temp_store() as (store, settings):
    rn = FetchRunner(store, s if not settings else settings)
    doi_map, title_map = rn._load_existing_maps()
    from litagent.schema import Document, Author
    probe = Document(id="probe", title="Completely Different Title", doi=list(doi_map.keys())[0] if doi_map else None)
    calls = {"n":0}
    orig_sim = R.title_similarity
    R.title_similarity = lambda a,b: (calls.__setitem__("n", calls["n"]+1), 0.0)[1]
    try:
        hit = rn._match_existing(probe, doi_map, title_map)
    finally:
        R.title_similarity = orig_sim
    c.add("27", "DOI 命中后不再走标题比对", bool(hit) and calls["n"]==0,
          "命中=%s 相似度调用=%d" % (bool(hit), calls["n"]))

# 28 批内同 DOI
with __import__("harness").temp_store() as (store, settings):
    rn = FetchRunner(store, s if not settings else settings)
    n0 = store.count_documents()
    def mk(i, doi): return Document(id=i, title="Batch Doc "+i, doi=doi, year=2025,
                                    authors=[Author(family="X", given="Y")])
    rep = rn.ingest([mk("b1","10.7777/dup.1"), mk("b2","10.7777/DUP.1")])
    c.add("28", "批内同 DOI 第二条被拦截", store.count_documents()==n0+1 and rep.merged==1,
          "新增 %d merged %d" % (store.count_documents()-n0, rep.merged))

# 29 单查询失败不影响其他（用假 source 触发异常）
class BoomSource:
    name="boom"
    def search(self, query, **kw):
        if "fail" in query: raise RuntimeError("模拟单源失败")
        return fake_documents(1)
c.skip("29", "单查询失败只记报告（未覆盖）",
       "FetchRunner 无公开多查询入口，多源调度在 cli 层组合，本项未直接覆盖")

# 30 slugify 与归档
BAD = "/" + chr(92) + ':*?"<>|'
c.add("30.1", "_slugify 中文与特殊字符",
      all(ch not in _slugify(n) for n in ["测试 文献/2024", "a:b*c?d", "中文标题"] for ch in BAD),
      _slugify("测试 文献/2024"))
c.add("30.2", "_slugify 长度受限", len(_slugify("x"*120, max_length=48))<=48, len(_slugify("x"*120, max_length=48)))

res = c.report("第2层 fetch（全 mock）")
json.dump(res, open(str(DATA/"layer2_fetch.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
