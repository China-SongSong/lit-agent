# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, sqlite3
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
s = get_settings()
c = sqlite3.connect(str(s.db_path))
print("最终状态: 文献=%d 含全文=%d 片段=%d 词条=%d 指纹=%d 总结=%d 缓存=%d" % (
  c.execute("select count(*) from documents").fetchone()[0],
  c.execute("select count(*) from documents where has_fulltext=1").fetchone()[0],
  c.execute("select count(*) from chunks").fetchone()[0],
  c.execute("select count(distinct term) from postings").fetchone()[0],
  c.execute("select count(*) from fingerprints").fetchone()[0],
  c.execute("select count(*) from summaries").fetchone()[0],
  c.execute("select count(*) from llm_cache").fetchone()[0]))
print("integrity:", c.execute("pragma integrity_check").fetchone()[0])
print("ingest_log 条数:", c.execute("select count(*) from ingest_log").fetchone()[0])
c.close()
