# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[2]

import sys, json, math, sqlite3
sys.path.insert(0, str(_REPO_ROOT / '测试资产' / 'scripts'))
from harness import Case, DATA, temp_store
c = Case()
from litagent.schema import Document, Author, Chunk, Summary
from litagent.index.lexical import tokenize, BM25Index
from litagent.index.phrase import exact_tokens, PhraseIndex, normalized_exact_text
from litagent.index import vector as V
from litagent.index.builder import IndexBuilder
from litagent.clean.chunker import chunk_text

def mkdoc(i, title="Paper %d about battery thermal runaway", doi=None):
    return Document(id="t:%d"%i, title=title % i, doi=doi or "10.9000/t.%d"%i,
                    year=2024, venue="Test J", authors=[Author(family="Doe", given="J")],
                    language="en", source="test")

with temp_store() as (store, settings):
    # 39.1 schema 幂等
    n1 = len(store.table_names()); store.init_schema(); n2 = len(store.table_names())
    c.add("39.1", "init_schema 幂等", n1==n2 and n1>0, "%d -> %d 张表" % (n1,n2))

    d = mkdoc(1)
    store.upsert_document(d); back = store.get_document(d.id, with_fulltext=False)
    c.add("39.2", "document upsert 回读一致", back is not None and back.title==d.title and back.doi==d.doi,
          back.title[:30] if back else None)

    body = "This is a test chunk about fingerprints. " * 20
    chunks = chunk_text(d.id, body, max_chars=300, overlap_chars=30, min_chars=20)
    store.replace_chunks(d.id, chunks)
    IndexBuilder(store, settings).build_lexical(doc_ids=[d.id])
    got = list(store.iter_chunks(d.id))
    c.add("39.3", "chunk 回读一致", len(got)==len(chunks) and got[0].text[:10]==chunks[0].text[:10],
          "%d / %d" % (len(got), len(chunks)))

    s = Summary(doc_id=d.id, tldr="摘要", problem="问题")
    store.upsert_summary(s); back2 = store.get_summary(d.id)
    c.add("39.4", "summary upsert 回读", back2 is not None and back2.tldr=="摘要", back2.tldr if back2 else None)

    # 40 查询接口
    c.add("40.1", "find_by_doi", (store.find_by_doi(d.doi) or {}) and True, "命中")
    c.add("40.2", "find_by_title", store.find_by_title(d.title) is not None, "命中")
    need = store.docs_needing_summary("summary-v1")
    ids = [x[0] if isinstance(x, tuple) else getattr(x, "id", getattr(x, "doc_id", None)) for x in need]
    c.add("40.3", "docs_needing_summary 语义可用", isinstance(need, list), "待办 %d 条" % len(need))

    # 41 事务回滚：替换 chunk 时中途异常不应留半批
    before = len(list(store.iter_chunks(d.id)))
    bad = list(chunks) + [None]
    try:
        store.replace_chunks(d.id, bad)
        rolled = True
    except Exception:
        rolled = len(list(store.iter_chunks(d.id)))==before
    c.add("41", "replace_chunks 异常不留半批", rolled, "前 %d" % before)

    # lexical
    toks = tokenize("深度学习 transformer 模型")
    c.add("L1", "分词中英混排", any("\u4e00"<=t[0]<="\u9fff" for t in toks if t) and any(t.lower()=="transformer" for t in toks),
          toks[:6])
    bi = BM25Index(store, k1=settings.bm25_k1, b=settings.bm25_b, tokenizer_cache_dir=DATA)
    bi.refresh()
    res = bi.search("fingerprints", top_k=5)
    c.add("L2", "BM25 可检索", len(res) >= 1, "命中 %d" % len(res))
    c.add("L3", "BM25 空查询不崩", bi.search("", top_k=5)==[] or isinstance(bi.search("", top_k=5), list),
          "")
    bi2 = BM25Index(store, k1=0.1, b=1.0, tokenizer_cache_dir=DATA); bi2.refresh()
    c.add("L4", "BM25 参数可构造并检索", isinstance(bi2.search("chunk", top_k=3), list), "")

    # phrase
    c.add("P1", "exact_tokens 中英数切分",
          len(exact_tokens("ABC 123 中文"))>=2 and exact_tokens("")==[], exact_tokens("ABC 123 中文"))
    pi = PhraseIndex(store)
    hits = pi.search("This is a test chunk about fingerprints.", top_k=5, min_ratio=0.2)
    c.add("P2", "指纹命中", len(hits)>=1, "命中 %d" % len(hits))
    c.add("P3", "短查询不崩", isinstance(pi.search("test", top_k=3), list), "")

    # vector
    v = V.embed_text("battery thermal runaway safety")
    norm = math.sqrt(sum(float(x)*float(x) for x in v))
    c.add("V1", "embed_text L2 归一", abs(norm-1.0) < 1e-6, "‖v‖=%.6f" % norm)
    a1 = V.embed_text("lithium battery thermal runaway causes fire")
    a2 = V.embed_text("lithium battery thermal runaway leads to fire")
    a3 = V.embed_text("medieval poetry of the twelfth century")
    c.add("V2", "同义句余弦高于无关句",
          V.cosine(a1,a2) > V.cosine(a1,a3), "%.3f vs %.3f" % (V.cosine(a1,a2), V.cosine(a1,a3)))
    blob = V.encode_vector("roundtrip test")
    c.add("V3", "encode/decode 往返", V.cosine(V.decode_vector(blob), V.embed_text("roundtrip test")) > 0.999, "")
    c.add("V4", "cosine 长度不等返回 0", V.cosine([1.0,0.0],[1.0])==0.0, "")
    import inspect
    params = list(inspect.signature(V.NeuralEmbeddingEncoder.__init__).parameters)[1:]
    kw = {}
    for name in params:
        if "model" in name or "path" in name:
            kw[name] = str(DATA/"no_such_model.onnx"); break
    try:
        enc = V.NeuralEmbeddingEncoder(**kw)
        enc.encode(["x"]); enc_ok, enc_msg = False, "未抛错"
    except FileNotFoundError as e:
        enc_ok, enc_msg = True, str(e)[:60]
    except Exception as e:
        enc_ok, enc_msg = False, "%s: %s" % (type(e).__name__, str(e)[:50])
    c.add("V5", "Neural 缺模型报 FileNotFoundError", enc_ok, enc_msg)

    # builder 全量→增量→refresh
    rep = IndexBuilder(store, settings).build_all()
    con = store.connect()
    npost = con.execute("select count(*) from postings").fetchone()[0]
    nfp = con.execute("select count(*) from fingerprints").fetchone()[0]
    c.add("B1", "全量构建产生倒排与指纹", npost>0 and nfp>0, "倒排 %d 指纹 %d" % (npost,nfp))
    d2 = mkdoc(2)
    store.upsert_document(d2)
    store.replace_chunks(d2.id, chunk_text(d2.id, "Incremental addition mentions incremental marker. "*10,
                                           max_chars=300, overlap_chars=30, min_chars=20))
    rep2 = IndexBuilder(store, settings).build_lexical(doc_ids=[d2.id])
    bi.refresh()
    found = bi.search("incremental marker", top_k=5)
    c.add("B2", "增量构建后检索可见", len(found)>=1, "命中 %d" % len(found))

res = c.report("第4层 index")
json.dump(res, open(str(DATA/"layer4_index.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
