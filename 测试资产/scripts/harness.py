# -*- coding: utf-8 -*-
"""测试基建：假 LLM、假数据源、临时库、金样例、迷你 PDF。"""

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[2]

import sys, os, json, tempfile, shutil, sqlite3
from contextlib import contextmanager

SCR = str(_REPO_ROOT / '作品相关代码' / 'scr')
if SCR not in sys.path:
    sys.path.insert(0, SCR)
DATA = Path(str(_REPO_ROOT / '测试资产' / 'data'))
DATA.mkdir(parents=True, exist_ok=True)

from litagent.llm.client import LLMUsage, LLMError

class FakeLLM:
    """队列式假客户端：脚本元素可为 dict / None / Exception / callable。"""
    def __init__(self, script=None):
        self.script = list(script or [])
        self.i = 0
        self.usage = LLMUsage()
        self.calls = 0
        self.messages = []
    def chat_json(self, messages, *, retries=2, **kw):
        self.calls += 1
        self.messages.append(list(messages))
        if self.i >= len(self.script):
            return {"final_answer": "（脚本耗尽）"}
        item = self.script[self.i]; self.i += 1
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item(messages)
        return item
    def chat(self, messages, **kw):
        self.calls += 1
        self.messages.append(list(messages))
        if self.i >= len(self.script):
            return "（脚本耗尽）"
        item = self.script[self.i]; self.i += 1
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
    def probe(self): return {"ok": True}
    def close(self): pass

@contextmanager
def temp_store(copy_production=True, corpus=False):
    """临时库：默认复制生产库，保证只读语义；用完自动删除。"""
    from litagent.index.store import Store
    from litagent.config import get_settings
    d = tempfile.mkdtemp(prefix="littest_")
    path = os.path.join(d, "index.db")
    if corpus:
        from litagent.evaluation.calibration import build_evaluation_store
        store, settings = build_evaluation_store(
            Path(SCR)/"tests/fixtures/calibration_corpus_v2.jsonl", Path(d))
        yield store, settings
    else:
        s = get_settings()
        if copy_production:
            src = sqlite3.connect(str(s.db_path)); dst = sqlite3.connect(path)
            src.backup(dst); dst.close(); src.close()
        store = Store(path); store.init_schema()
        yield store, s
    try:
        store.close()
    except Exception:
        pass
    shutil.rmtree(d, ignore_errors=True)

def fake_documents(n=2, doi_prefix="10.9999/test"):
    from litagent.schema import Document, Author
    out = []
    for i in range(n):
        out.append(Document(id="fake:%d" % i, title="Synthetic Paper %d on Battery Safety" % i,
                            doi="%s.%d" % (doi_prefix, i), year=2025,
                            venue="Journal of Synthetic Tests", venue_type="journal",
                            abstract="Synthetic abstract %d." % i, language="en",
                            source="fake", authors=[Author(family="Doe", given="Jane")]))
    return out

def make_fake_source(docs=None):
    """实现 Source 接口的假数据源，隔离网络。"""
    from litagent.fetch.base import Source, register_source
    payload = docs if docs is not None else fake_documents(2)
    class FakeSource(Source):
        name = "fake"
        def __init__(self, settings=None, **kw):
            self.settings = settings
            self.queries = []
        def search(self, query, *, limit=10, **filters):
            self.queries.append(query)
            return list(payload)[:limit]
    return FakeSource

GOLDEN = {
    "journal_en": {"doc": {"id":"g1","title":"Understanding Thermal Runaway in Lithium-Ion Batteries",
        "authors":[{"family":"Liu","given":"Chenchen"},{"family":"Dai","given":"Hai"},
                   {"family":"Wang","given":"Danyang"},{"family":"Ren","given":"Xiaobo"}],
        "year":2024,"venue":"Journal of The Electrochemical Society","venue_type":"journal",
        "volume":"171","issue":"12","pages":"120527","language":"en"},
        "gbt": "LIU C, DAI H, WANG D, et al. Understanding Thermal Runaway in Lithium-Ion Batteries[J]. Journal of The Electrochemical Society, 2024, 171(12): 120527."},
    "journal_zh": {"doc": {"id":"g2","title":"图神经网络推荐中的元学习冷启动方法",
        "authors":[{"family":"张","given":"三"},{"family":"李","given":"四"}],
        "year":2025,"venue":"计算机学报","venue_type":"journal","volume":"48","issue":"3","pages":"1-18","language":"zh"},
        "gbt": "张三, 李四. 图神经网络推荐中的元学习冷启动方法[J]. 计算机学报, 2025, 48(3): 1-18."},
    "guji": {"doc": {"id":"g3","book_title":"史记","chapter_title":"项羽本纪","chapter_index":"7",
        "authors":[{"family":"司马迁","given":""}],"year":"1959","publisher":"中华书局",
        "publisher_place":"北京","edition":"点校本","language":"zh","extra":{"collator":"点校者"}},
        "first": "《史记》卷7《项羽本纪》，点校者，北京：中华书局，点校本，第295页。",
        "repeat": "《史记》卷7《项羽本纪》，点校者，第295页。"},
}

def make_pdf_fixtures(target=None):
    """生成四份迷你 PDF：正常、双栏、扫描型（无文本层）、带页眉页脚。"""
    import fitz
    target = Path(target or (DATA / "pdf_fixtures")); target.mkdir(parents=True, exist_ok=True)
    out = {}
    body = ("Citation tracing systems must separate exact textual evidence from semantic similarity. "
            "A deterministic fingerprint index verifies continuous phrase overlap and reports character spans.") * 3
    p = target / "plain.pdf"
    d = fitz.open(); pg = d.new_page()
    pg.insert_textbox(fitz.Rect(50, 60, 545, 760), body, fontsize=9)
    d.save(p); d.close(); out["plain"] = p
    p = target / "twocolumn.pdf"
    d = fitz.open(); pg = d.new_page()
    left = fitz.Rect(50, 60, 290, 760); right = fitz.Rect(305, 60, 545, 760)
    pg.insert_textbox(left, body, fontsize=8); pg.insert_textbox(right, body[::-1], fontsize=8)
    d.save(p); d.close(); out["twocolumn"] = p
    p = target / "scanned.pdf"
    d = fitz.open(); pg = d.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 400, 500), 0)
    pix.set_rect(fitz.IRect(0, 0, 400, 500), (235, 235, 235))
    pg.insert_image(fitz.Rect(50, 60, 545, 760), pixmap=pix)
    d.save(p); d.close(); out["scanned"] = p
    p = target / "headerfooter.pdf"
    d = fitz.open()
    for i in range(3):
        pg = d.new_page()
        pg.insert_textbox(fitz.Rect(50, 20, 545, 45), "Journal of Synthetic Tests Vol. 1", fontsize=8)
        pg.insert_textbox(fitz.Rect(50, 60, 545, 700), body, fontsize=9)
        pg.insert_textbox(fitz.Rect(50, 745, 545, 770), "- %d -" % (i + 1), fontsize=8)
    d.save(p); d.close(); out["headerfooter"] = p
    return out

class Case:
    def __init__(self): self.rows = []
    def add(self, no, name, ok, detail=""):
        self.rows.append(dict(no=no, name=name, ok=bool(ok), status="pass" if ok else "fail", detail=str(detail)[:260]))
        print("%-6s %-4s %-46s %s" % ("PASS" if ok else "FAIL", no, name, str(detail)[:88]))
    def skip(self, no, name, reason=""):
        self.rows.append(dict(no=no, name=name, ok=True, status="skip", detail=str(reason)[:260]))
        print("%-6s %-4s %-46s %s" % ("SKIP", no, name, str(reason)[:88]))
    def report(self, title):
        f = [r for r in self.rows if r["status"] == "fail"]
        s = [r for r in self.rows if r["status"] == "skip"]
        print("\n== %s：%d 项，通过 %d、跳过 %d、失败 %d ==" % (
            title, len(self.rows), len(self.rows)-len(f)-len(s), len(s), len(f)))
        for r in s: print("  SKIP %s %s | %s" % (r["no"], r["name"], r["detail"][:120]))
        for r in f: print("  FAIL %s %s | %s" % (r["no"], r["name"], r["detail"][:150]))
        return {"title": title, "total": len(self.rows), "failed": len(f), "skipped": len(s), "rows": self.rows}
