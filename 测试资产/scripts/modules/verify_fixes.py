# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, os, json, subprocess, textwrap, sqlite3
SCR = str(_REPO_ROOT / '作品相关代码' / 'scr')
sys.path.insert(0, SCR)
from litagent.config import get_settings, build_settings, Settings
RES=[]
def rec(name, ok, detail=""):
    RES.append(dict(name=name, ok=bool(ok), detail=str(detail)[:260]))
    print("%-6s %-40s %s" % ("PASS" if ok else "FAIL", name, str(detail)[:100]))

s = get_settings()

# 1) 基线哈希（新口径）
from litagent.evaluation.corpus_build import logical_database_sha256, logical_database_table_sha256
fx = json.load(open(os.path.join(SCR,"tests","fixtures","production_baseline_v1.json"), encoding="utf-8"))
h = logical_database_sha256(s.db_path)
tabs = logical_database_table_sha256(s.db_path)
rec("基线哈希与清单一致", h == fx.get("logical_sha256"), "%s vs %s" % (h[:16], str(fx.get("logical_sha256"))[:16]))
rec("哈希只覆盖书目表", "summaries" not in tabs and "meta" not in tabs, list(tabs.keys()))

# 2) 分块上限 0 / 负数（子进程 + 超时）
PY = sys.executable
TPL = textwrap.dedent('''
    import sys, sqlite3
    sys.path.insert(0, r"%s")
    from litagent.config import get_settings
    from litagent.index.store import Store
    from litagent.clean.chunker import chunk_text
    base = get_settings(); Store(base.db_path).init_schema()
    con = sqlite3.connect(str(base.db_path))
    raw = con.execute("select text from chunk_text where length(text)>1500 limit 1").fetchone()[0]
    cs = chunk_text("t", (raw+"\\n\\n")*3, max_chars=%d, overlap_chars=0, min_chars=1)
    mx = max((len(c.text) for c in cs), default=0)
    print("OK chunks=%%d max=%%d" %% (len(cs), mx))
''') % (SCR.replace("\\","\\\\"), 0)
for name, mc in [("max_chars=0",0), ("max_chars=-1",-1), ("max_chars=1",1)]:
    code = TPL.replace("max_chars=0", "max_chars=%d" % mc)
    try:
        r = subprocess.run([PY,"-c",code], capture_output=True, text=True, timeout=15, encoding="utf-8", errors="replace")
        out = (r.stdout or "").strip() or ((r.stderr or "").strip().splitlines() or ["<无>"])[-1][:70]
        rec("分块上限 %s 可正常返回" % name, out.startswith("OK"), out[:80])
    except subprocess.TimeoutExpired:
        rec("分块上限 %s 可正常返回" % name, False, "超时（仍死循环）")

# 3) 参数边界
def env_case(env, val, field):
    old = os.environ.get(env); os.environ[env]=val
    try: return getattr(build_settings(), field)
    finally:
        if old is None: os.environ.pop(env, None)
        else: os.environ[env]=old
rec("分块上限 0 回退默认", env_case("LIT_MAX_CHUNK_CHARS","0","max_chunk_chars") == Settings.max_chunk_chars,
    env_case("LIT_MAX_CHUNK_CHARS","0","max_chunk_chars"))
rec("分块上限负数回退默认", env_case("LIT_MAX_CHUNK_CHARS","-5","max_chunk_chars") == Settings.max_chunk_chars,
    env_case("LIT_MAX_CHUNK_CHARS","-5","max_chunk_chars"))
rec("合法非默认值保留", env_case("LIT_MAX_CHUNK_CHARS","900","max_chunk_chars") == 900, 900)
rec("重叠字符为负回退默认", env_case("LIT_CHUNK_OVERLAP_CHARS","-10","chunk_overlap_chars") == Settings.chunk_overlap_chars,
    env_case("LIT_CHUNK_OVERLAP_CHARS","-10","chunk_overlap_chars"))
try:
    st = Settings(max_chunk_chars=0)
    rec("直接构造 Settings 也被约束", st.max_chunk_chars == Settings.max_chunk_chars, st.max_chunk_chars)
except Exception as e:
    rec("直接构造 Settings 也被约束", False, e)

fails=[x for x in RES if not x["ok"]]
print("\n== 修复验证：%d 项，失败 %d ==" % (len(RES), len(fails)))
for f in fails: print("  FAIL:", f["name"], f["detail"][:120])
