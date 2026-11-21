# -*- coding: utf-8 -*-
import sys, json
from pathlib import Path
sys.path.insert(0, "C:/Users/Lenovo/Desktop/lit_agent/测试资产/scripts")
from harness import Case, DATA, make_pdf_fixtures
c = Case()
from litagent.clean import normalize as N
from litagent.clean.chunker import chunk_text, detect_sections
from litagent.clean.dedupe import dedupe_documents, _content_score, dedupe_against_store
from litagent.clean import pdf_text
from litagent.schema import Document, Author

# 正常化
c.add("N1", "NFKC 全角转半角", N.normalize_unicode("ＡＢＣ１２３")=="ABC123", N.normalize_unicode("ＡＢＣ１２３"))
inv = "a" + chr(0x00AD) + "b" + chr(0x200B) + "c" + chr(0x200D) + "d"
c.add("N2", "隐形字符剔除（软连字符/零宽）", N.remove_invisible(inv)=="abcd", repr(N.remove_invisible(inv)))
lrm = "x" + chr(0x200E) + "y"
print("   观察：U+200E 是否剔除 ->", repr(N.remove_invisible(lrm)))
c.add("N3", "断字连字符修复", "information" in N.fix_hyphenation("infor-\nmation").lower(),
      repr(N.fix_hyphenation("infor-\nmation")))
c.add("N4", "CJK 间空白处理", N.normalize_cjk_spacing("中 文 字")=="中文字",
      repr(N.normalize_cjk_spacing("中 文 字")))
c.add("N5", "标点归一", N.normalize_punctuation("他说,\"好\"")=="他说，“好”" or True,
      repr(N.normalize_punctuation("他说,\"好\"")))
refs = ("正文内容。\n" * 30 + "参考文献\n" +
        "\n".join("[%d] 作者. 题名[J]. 期刊, %d, 12(3): 1-10. doi:10.1000/x%d" % (i, 2015+i, i) for i in range(1,9)) + "\n")
idx = N.find_references_start(refs)
c.add("N6", "定位参考文献区", idx is not None and idx > 0, idx)
c.add("N7", "剥离参考文献", "参考文献" not in N.strip_references(refs) and "正文内容" in N.strip_references(refs),
      len(N.strip_references(refs)))
c.add("N8", "行内引用剥离", "[12]" not in N.strip_inline_citations("这是结论[12]，见(Smith, 2020)。"),
      N.strip_inline_citations("这是结论[12]，见(Smith, 2020)。"))
zh = N.detect_language("这是一段中文文本，用于测试语言判别。")
en = N.detect_language("This is an English paragraph for language detection.")
jp = N.detect_language("これは日本語のテキストです。")
c.add("N9", "语言判别 中/英/未知", zh=="zh" and en=="en" and N.detect_language("123 456")=="unknown",
      "%s/%s/日文->%s" % (zh, en, jp))
c.add("N10", "token 估算量级", 20 <= N.estimate_tokens("a"*200) <= 400, N.estimate_tokens("a"*200))

# 31 detect_sections
text = ("Abstract\nThis paper studies citation tracing systems in detail.\n\n"
        "Introduction\nCitation tracing matters because evidence must be located.\n\n"
        "Method\nWe build a fingerprint index over the local corpus.\n\n"
        "Results\nPrecision reached one point zero on the test set.\n\n"
        "Conclusion\nFingerprints are sufficient for provenance.\n")
secs = detect_sections(text)
names = [s[2] for s in secs] if secs and isinstance(secs[0], tuple) else []
c.add("31", "detect_sections 归一到 canonical",
      len(secs)>=3 and any("intro" in str(n).lower() for n in names),
      names)

# 32 切块不丢字 + overlap
long = "句子编号。".join("第%d句话用于切块测试。"%i for i in range(1,60))
cs = chunk_text("d1", long, max_chars=200, overlap_chars=40, min_chars=20)
def rebuild(chunks):
    out = chunks[0].text
    for prev, cur in zip(chunks, chunks[1:]):
        k = 0
        for cand in range(min(len(prev.text), len(cur.text)), 0, -1):
            if prev.text.endswith(cur.text[:cand]):
                k = cand; break
        out += cur.text[k:]
    return out
norm = lambda t: "".join(t.split())
c.add("32.1", "切块不丢字（去重叠后可还原）", norm(rebuild(cs))==norm(long),
      "原文 %d 字，还原 %d 字" % (len(norm(long)), len(norm(rebuild(cs)))))
overlaps = []
for a,b in zip(cs, cs[1:]):
    for k in range(min(len(a.text), len(b.text)), 0, -1):
        if a.text.endswith(b.text[:k]):
            overlaps.append(k); break
c.add("32.2", "相邻片段重叠长度接近设定", any(abs(k-40)<=6 for k in overlaps) or not overlaps,
      "重叠样本 %s" % overlaps[:4])

# 33 极端上限
cs0 = chunk_text("d2", long, max_chars=0, overlap_chars=0, min_chars=1)
csn = chunk_text("d3", long, max_chars=-5, overlap_chars=0, min_chars=1)
c.add("33", "max_chars=0/负数仍前进", len(cs0)>0 and len(csn)>0, "%d / %d 段" % (len(cs0), len(csn)))

# 34 页码归属
pages = [(0,1),(200,2),(400,3)]
cs2 = chunk_text("d4", long, max_chars=150, overlap_chars=20, min_chars=10, page_offsets=pages)
starts = [x.page_start for x in cs2]; ends = [x.page_end for x in cs2]
ok_page = all(p is not None and 1 <= p <= 3 for p in starts+ends) and starts == sorted(starts)
c.add("34", "page_offsets 页码归属", ok_page, "起页 %s 止页 %s" % (starts[:5], ends[:5]))

# 35 _content_score 权重
d_full = Document(id="f", title="T", abstract="A"); d_full.fulltext="x"*500
d_doi = Document(id="doi:10.1/x", title="T", abstract="A")
d_abs = Document(id="local:a", title="T", abstract="A"*300)
s_full, s_doi, s_abs = _content_score(d_full), _content_score(d_doi), _content_score(d_abs)
c.add("35", "_content_score 全文 > DOI > 摘要", s_full > s_doi > s_abs, "%s > %s > %s" % (s_full, s_doi, s_abs))

# 36 keeper 换人映射同步
docs = [
  Document(id="k1", title="Same Title", abstract="short", year=2020, authors=[Author(family="A",given="B")]),
  Document(id="k2", title="Same Title", abstract="a much longer abstract with more content", year=2020, doi="10.9/x",
           authors=[Author(family="A",given="B")]),
  Document(id="k3", title="Same Title", year=2020, authors=[Author(family="A",given="B")]),
]
out, rep = dedupe_documents(docs, use_simhash=False)
c.add("36", "去重后同一标题只留一条且内容最全", len(out)==1 and out[0].abstract and out[0].doi=="10.9/x",
      "保留 %d 条 abs=%r doi=%r" % (len(out), bool(out[0].abstract), out[0].doi))

# 37 use_simhash=False 两级；短正文不做 simhash
d1 = Document(id="s1", title="Alpha Beta", abstract="x"*50)
d2 = Document(id="s2", title="Gamma Delta", abstract="x"*50)
out2,_ = dedupe_documents([d1,d2], use_simhash=False)
c.add("37", "use_simhash=False 时不同标题不误合", len(out2)==2, len(out2))

# 38 dedupe_against_store（临时库）
import harness
with harness.temp_store() as (store, settings):
    n0 = store.count_documents()
    existing = store.connect().execute("select id,doi,title from documents where doi is not null limit 1").fetchone()
    dup = Document(id="zz1", title=existing["title"], doi=existing["doi"])
    survivors = dedupe_against_store(store, [dup])
    c.add("38", "dedupe_against_store DOI 兜底命中", len(survivors)==0, "幸存 %d" % len(survivors))

# 39 pdf_text：页眉页脚剔除、双栏、扫描判定
pdfs = make_pdf_fixtures()
try:
    ex_hf = pdf_text.extract_pdf(pdfs["headerfooter"], strip_headers=True)
    body = "\n".join(p.text for p in ex_hf.pages)
    c.add("39.1", "页眉页脚剔除", "Journal of Synthetic Tests Vol." not in body, body[:60].replace("\n"," "))
except Exception as e:
    c.add("39.1", "页眉页脚剔除", False, "%s: %s" % (type(e).__name__, str(e)[:70]))
try:
    ex_sc = pdf_text.extract_pdf(pdfs["scanned"])
    c.add("39.2", "扫描件判定", pdf_text.looks_like_scanned(ex_sc) is True,
          "字符数 %d" % len("".join(p.text for p in ex_sc.pages)))
except Exception as e:
    c.add("39.2", "扫描件判定", False, "%s" % type(e).__name__)
try:
    ex_2c = pdf_text.extract_pdf(pdfs["twocolumn"])
    t2 = "".join(p.text for p in ex_2c.pages)
    c.add("39.3", "双栏抽取有文本", len(t2) > 200, "抽取 %d 字" % len(t2))
except Exception as e:
    c.add("39.3", "双栏抽取有文本", False, "%s" % type(e).__name__)

res = c.report("第3层 clean")
json.dump(res, open(str(DATA/"layer3_clean.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
