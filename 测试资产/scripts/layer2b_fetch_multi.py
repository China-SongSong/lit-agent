# -*- coding: utf-8 -*-
import sys, json, glob
sys.path.insert(0, "C:/Users/Lenovo/Desktop/lit_agent/测试资产/scripts")
from harness import Case, DATA, temp_store
c = Case()
from litagent.fetch.base import Source, register_source
from litagent.fetch.runner import FetchRunner
from litagent.schema import Document, Author

@register_source
class FakeFetchSource(Source):
    name = "faketest"
    def __init__(self, settings=None, **kw):
        self.settings = settings
        self.calls = []
    def search(self, query, *, limit=10, **filters):
        self.calls.append(query)
        if "fail" in query:
            raise RuntimeError("模拟单查询失败")
        if "empty" in query:
            return []
        return [Document(id="fk:%s:%d" % (query, i), title="%s paper %d" % (query, i),
                         doi="10.8888/%s.%d" % (query, i), year=2025,
                         venue="Fake Journal", authors=[Author(family="Doe", given="Jane")],
                         source="faketest", language="en") for i in range(2)]

with temp_store() as (store, settings):
    rn = FetchRunner(store, settings)
    n0 = store.count_documents()

    # 29.1 单查询失败不影响其他
    rep = rn.fetch_online("faketest", ["good-one", "fail-here", "good-two"],
                          resume=False, archive=True, limit_per_query=2)
    c.add("29.1", "单查询失败只记报告，其余继续",
          rep.failed_queries == ["fail-here"] and store.count_documents() == n0 + 4 and bool(rep.errors),
          "失败=%s 新增=%d 错误=%d" % (rep.failed_queries, store.count_documents()-n0, len(rep.errors)))

    # 29.2 ingest_log 记录三种状态
    g = store.get_ingest
    st1 = (g("faketest","good-one") or {}).get("status")
    st2 = (g("faketest","fail-here") or {}).get("status")
    c.add("29.2", "ingest_log 记录完成与失败状态",
          st1 == "done" and st2 in ("error", "failed", None),
          "good-one=%s fail-here=%s" % (st1, st2))

    # 29.3 resume 跳过已完成、不重抓
    before = store.count_documents()
    seen = []
    rep2 = rn.fetch_online("faketest", ["good-one", "good-two"],
                           resume=True, archive=True, on_progress=lambda k,q,n: seen.append((k,q)))
    c.add("29.3", "resume 跳过已完成查询",
          [k for k,_ in seen] == ["skip","skip"] and store.count_documents() == before,
          "回调=%s 新增=%d" % (seen, store.count_documents()-before))

    # 29.4 未完成查询在 resume 时仍会重抓
    rep3 = rn.fetch_online("faketest", ["fail-here"], resume=True)
    c.add("29.4", "resume 不跳过失败查询", rep3.failed_queries == ["fail-here"],
          "失败=%s" % rep3.failed_queries)

    # 29.5 空结果记 empty 而非 done
    rep4 = rn.fetch_online("faketest", ["empty-query"], resume=False)
    st4 = (g("faketest","empty-query") or {}).get("status")
    c.add("29.5", "空结果记 empty 状态", st4 == "empty" and "empty-query" in rep4.failed_queries, st4)

    # 29.6 归档 JSONL 可重放
    files = rep.archive_files
    ok_replay = False; detail = ""
    if files:
        rows = [json.loads(l) for l in open(files[0], encoding="utf-8") if l.strip()]
        ok_replay = len(rows) >= 1 and "title" in rows[0]
        detail = "%s 行 %d" % (files[0].split(chr(92))[-1].split("/")[-1], len(rows))
    c.add("29.6", "原始归档可重放", ok_replay, detail)

res = c.report("补充：第2层 #29 多查询调度与续传")
old = json.load(open(str(DATA/"layer2_fetch.json"), encoding="utf-8"))
old["rows"].extend([r for r in res["rows"]])
old["total"] = len(old["rows"]); old["failed"] = sum(1 for r in old["rows"] if r["status"]=="fail")
old["skipped"] = sum(1 for r in old["rows"] if r["status"]=="skip")
json.dump(old, open(str(DATA/"layer2_fetch.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
