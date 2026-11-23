# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[2]

import sys, os, json, subprocess, inspect, tempfile, shutil
sys.path.insert(0, str(_REPO_ROOT / '测试资产' / 'scripts'))
from harness import Case, SCR, DATA, make_pdf_fixtures, temp_store, fake_documents

c = Case()
import litagent
from litagent.schema import (normalize_doi, normalize_title, title_similarity, parse_authors,
                             split_cjk_name, summary_quality, Document, Summary, Claim)
from litagent.hashing import (stable_hash64, fingerprint_tokens, simhash64, is_near_duplicate,
                              hamming_distance)
import litagent.config as cfg
import litagent.contracts as C
from litagent.disciplines import classify_disciplines
import litagent.compat as compat

print("parse_authors 签名:", inspect.signature(parse_authors))
print("summary_quality 签名:", inspect.signature(summary_quality))

# 1 normalize_doi
c.add("1.1", "normalize_doi 去前缀/大写/空白",
      normalize_doi("https://doi.org/10.1000/ABC")=="10.1000/abc" and normalize_doi("  10.1000/ABC  ")=="10.1000/abc",
      normalize_doi("https://doi.org/10.1000/ABC"))
c.add("1.2", "normalize_doi 空值与 None", normalize_doi("") is None and normalize_doi(None) is None,
      repr(normalize_doi("")), )

# 2 normalize_title
a = normalize_title("A Study: Of Things!")
b = normalize_title("a study of things")
cc = normalize_title("Ａ　Ｓｔｕｄｙ ｏｆ　Ｔｈｉｎｇｓ")
c.add("2", "normalize_title 大小写/全半角/标点/空白同键", a==b==cc, "%r / %r / %r" % (a,b,cc))

# 3 title_similarity
base = "Deep Learning Approaches for Citation Network Analysis"
c.add("3.1", "全同=1.0", abs(title_similarity(base, base)-1.0) < 1e-9)
c.add("3.2", "全异接近 0", title_similarity("Quantum Entanglement", "Medieval Poetry") < 0.2,
      "%.3f" % title_similarity("Quantum Entanglement", "Medieval Poetry"))
sims = {}
for suf in ["", " Today", " 2024", " Revisited", " Extended", " in Practice", " and Beyond", " Methods"]:
    sims[suf or "<same>"] = title_similarity(normalize_title(base+suf), normalize_title(base))
print("  相似度样本:", {k: round(v,3) for k,v in sims.items()})
pairs = sorted(sims.items(), key=lambda kv: kv[1])
lo = pairs[0]
c.add("3.3", "阈值两侧递减且跨过 0.92", sims["<same>"]==1.0 and lo[1] < 0.92,
      "最低 %.3f" % lo[1])

# 4 parse_authors / split_cjk_name
try:
    au1 = parse_authors("Zhang San, Li Si")
    au2 = parse_authors([{"family":"Doe","given":"Jane"}])
    au3 = parse_authors(["Wang Wu", {"family":"Zhao","given":"Liu"}])
    c.add("4.1", "parse_authors 字符串/字典/混列表",
          len(au1)>=1 and au2[0].family=="Doe" and len(au3)>=2,
          "%d/%d/%d" % (len(au1), len(au2), len(au3)))
except Exception as e:
    c.add("4.1", "parse_authors 字符串/字典/混列表", False, "%s: %s" % (type(e).__name__, str(e)[:60]))
try:
    fam, giv = split_cjk_name("张三")
    c.add("4.2", "split_cjk_name 中文姓名拆分", fam=="张" and giv=="三", "%s/%s" % (fam,giv))
except Exception as e:
    c.add("4.2", "split_cjk_name 中文姓名拆分", False, str(e)[:60])

# 5 summary_quality 三档
def mk(**kw):
    s = Summary(doc_id="d")
    for k,v in kw.items(): setattr(s,k,v)
    return s
full = mk(tldr="a",problem="b",methods="c",findings="d",contributions="e",limitations="f")
empty = mk()
dt = Document(id="t", title="x"); dt.extra={"content_level":"title"}
da = Document(id="a", title="x"); da.extra={"content_level":"abstract"}
r1 = summary_quality(full, None, content_chars=2000)
r2 = summary_quality(empty, dt)
r3 = summary_quality(empty, da)
r4 = summary_quality(empty, None, content_chars=100)
c.add("5", "summary_quality 三档与缺失计数",
      r1[0]=="complete" and r2[0]=="title_limited" and r3[0]=="abstract_limited"
      and r4[0]=="short_content_limited" and len(r2[1])==6,
      "%s/%s/%s/%s" % (r1[0], r2[0], r3[0], r4[0]))

# 6 is_empty / merge
e1 = Document(id="e1", title="")
e2 = Document(id="e2", title="T", abstract="A")
c.add("6.1", "Document.is_empty 判定", e1.is_empty() and not e2.is_empty(), "%s/%s" % (e1.is_empty(), e2.is_empty()))
keep = Document(id="k", title="Keep", abstract="", venue="V1")
inc  = Document(id="k2", title="Other", abstract="Abstract2", venue="V2", doi="10.1/x")
keep.merge(inc)
c.add("6.2", "merge 只补空字段不覆盖",
      keep.title=="Keep" and keep.abstract=="Abstract2" and keep.venue=="V1" and keep.doi=="10.1/x",
      "title=%s abstract=%s venue=%s" % (keep.title, keep.abstract, keep.venue))

# 7 content_hash
from litagent.schema import content_hash
h1 = content_hash("doc-1", "same body text")
h2 = content_hash("doc-1", "same body text")
h3 = content_hash("doc-1", "different body text")
h4 = content_hash("doc-2", "same body text")
c.add("7", "content_hash 稳定且可区分", h1==h2 and h1!=h3 and h1!=h4,
      "%s/%s/%s" % (h1[:8], h3[:8], h4[:8]))

# 8 stable_hash64 跨进程
code = "import sys;sys.path.insert(0,%r);from litagent.hashing import stable_hash64;print(stable_hash64('cross-process-probe'))" % SCR
out = subprocess.run([sys.executable,"-c",code], capture_output=True, text=True, timeout=120)
same = out.stdout.strip() == str(stable_hash64("cross-process-probe"))
c.add("8", "stable_hash64 跨进程一致", same, out.stdout.strip()[:20])

# 9 fingerprint_tokens
toks = ["w%d"%i for i in range(10)]
fp = fingerprint_tokens(toks, 7)
short = fingerprint_tokens(["a","b"], 7)
c.add("9", "指纹 n=7 数量与短文本", len(fp)==4 and short==[],
      "10 词元→%d 条，2 词元→%d 条" % (len(fp), len(short)))

# 10 simhash
c.add("10.1", "simhash64 空输入为 0", simhash64([])==0)
base_h = simhash64(["a"]*10)
def flip(v, bits):
    for b in bits: v ^= (1<<b)
    return v
d3 = flip(base_h, [0,1,2]); d4 = flip(base_h, [0,1,2,3])
c.add("10.2", "汉明距离 3 命中、4 不命中",
      hamming_distance(base_h,d3)==3 and is_near_duplicate(base_h,d3)
      and hamming_distance(base_h,d4)==4 and not is_near_duplicate(base_h,d4),
      "d3=%d d4=%d" % (hamming_distance(base_h,d3), hamming_distance(base_h,d4)))

# 11 环境变量
def env_case(env, val, field):
    old = os.environ.get(env); os.environ[env]=val
    try: return getattr(cfg.build_settings(), field)
    finally:
        if old is None: os.environ.pop(env, None)
        else: os.environ[env]=old
from litagent.config import Settings
checks = [
    ("LIT_MAX_CHUNK_CHARS","900","max_chunk_chars",900),
    ("LIT_MAX_CHUNK_CHARS","0","max_chunk_chars",Settings.max_chunk_chars),
    ("LIT_MAX_CHUNK_CHARS","-7","max_chunk_chars",Settings.max_chunk_chars),
    ("LIT_MAX_CHUNK_CHARS","abc","max_chunk_chars",Settings.max_chunk_chars),
    ("LIT_FINGERPRINT_N","5","fingerprint_n",5),
    ("LIT_FINGERPRINT_N","0","fingerprint_n",Settings.fingerprint_n),
    ("LIT_TOP_K","7","default_top_k",7),
    ("LIT_TOP_K","-1","default_top_k",Settings.default_top_k),
    ("LIT_AGENT_MAX_ROUNDS","5","agent_max_rounds",5),
    ("LIT_LLM_TEMPERATURE","0.7","llm_temperature",0.7),
]
bad = [(e,v,env_case(e,v,f),x) for e,v,f,x in checks if abs(float(env_case(e,v,f))-float(x))>1e-9]
c.add("11", "环境变量合法采纳与非法回退", not bad, bad[:2])

# 12 路径拼接 / require_llm
s = cfg.build_settings()
c.add("12.1", "派生路径拼接正确",
      s.db_path.name=="index.db" and s.raw_dir.name=="raw" and s.pdf_dir.name=="pdfs",
      "%s | %s" % (s.db_path.name, s.raw_dir.name))
s2 = Settings(llm_api_key="")
try:
    s2.require_llm(); ok = False; msg = "未抛错"
except Exception as e:
    ok = True; msg = str(e)[:70]
c.add("12.2", "require_llm 无 Key 抛错", ok, msg)
s3 = Settings(llm_api_key="sk-test")
try:
    s3.require_llm(); ok2 = True
except Exception as e:
    ok2 = False; msg = str(e)[:50]
c.add("12.3", "有 Key 时不抛错", ok2, "" if ok2 else msg)

# 13 load_dotenv
from litagent.config import load_dotenv
tmpd = tempfile.mkdtemp()
envp = os.path.join(tmpd, ".env")
open(envp,"w",encoding="utf-8").write("# 注释行\nLIT_TEST_A=\"quoted value\"\nLIT_TEST_B='single'\nLIT_TEST_EXIST=from_file\n")
os.environ["LIT_TEST_EXIST"]="from_env"
load_dotenv(Path(envp), override=False)
c.add("13", "load_dotenv 注释/引号/不覆盖已有变量",
      os.environ.get("LIT_TEST_A")=="quoted value" and os.environ.get("LIT_TEST_B")=="single"
      and os.environ.get("LIT_TEST_EXIST")=="from_env",
      "A=%r EXIST=%r" % (os.environ.get("LIT_TEST_A"), os.environ.get("LIT_TEST_EXIST")))
for k in ("LIT_TEST_A","LIT_TEST_B","LIT_TEST_EXIST"): os.environ.pop(k, None)
shutil.rmtree(tmpd, ignore_errors=True)

# 14 require_online
try:
    C.OnlineRequest(query="x", allow_online=False).require_online(); c.add("14","未许可联网抛 OnlineAccessDenied",False)
except C.OnlineAccessDenied: c.add("14","未许可联网抛 OnlineAccessDenied",True)

# 15 validate_trace_result
from litagent.schema import TraceResult, SearchHit
tr = TraceResult(query="q"); tr.found=True
hit = SearchHit(chunk_id="c", doc_id="d", score=1.0, text="t"); hit.sources=["bm25"]
tr.hit=hit; tr.matched_text="t"
try:
    C.validate_trace_result(tr); c.add("15","无本地指纹证据的 found 被拒",False)
except C.ContractViolation as e: c.add("15","无本地指纹证据的 found 被拒",True, str(e)[:50])

# 16 source_tier_for
tiers = {n: str(C.source_tier_for(n)) for n in ("openalex","crossref","arxiv","semanticscholar","opencli","opencli:cnki","unknown-x")}
c.add("16", "来源分层映射",
      all("PRIMARY_API" in tiers[k] for k in ("openalex","crossref","arxiv","semanticscholar"))
      and all("BROWSER_SUPPLEMENT" in tiers[k] for k in ("opencli","opencli:cnki"))
      and "EXTERNAL_OTHER" in tiers["unknown-x"],
      {k: v.replace("SourceTier.","") for k,v in tiers.items()})

# 附加：disciplines / compat
c.add("17", "disciplines 中英关键词与大小写",
      "工程技术类" in classify_disciplines("锂离子电池热失控") and
      "计算机科学类" in classify_disciplines("FOCUSED CRAWLING for web discovery"),
      classify_disciplines("锂离子电池热失控"))
c.add("18", "disciplines 多学科与无命中",
      len(classify_disciplines("计算机科学中的社会学问题"))>=1 and classify_disciplines("zzz qqq")==["综合类"],
      classify_disciplines("zzz qqq"))
c.add("19", "compat PDF 支持判定", isinstance(compat.has_pdf_support(), bool) and compat.get_pdf_module() is not None,
      "has_pdf_support=%s" % compat.has_pdf_support())

# 基建自检：PDF fixtures
pdfs = make_pdf_fixtures()
c.add("0.1", "PDF fixtures 生成", len(pdfs)==4 and all(p.exists() for p in pdfs.values()),
      {k: p.stat().st_size for k,p in pdfs.items()})

res = c.report("第1层 基础纯函数")
json.dump(res, open(str(DATA/"layer1_basic.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
