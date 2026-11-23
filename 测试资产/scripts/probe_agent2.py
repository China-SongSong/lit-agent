# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[2]

import sys, json
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.agent.tools import LiteratureTools
from litagent.agent import prompts as P
s = get_settings(); store = Store(s.db_path); store.init_schema()
small = s.__class__(**{**s.__dict__, "agent_observation_chars": 50})
print("small.agent_observation_chars =", small.agent_observation_chars)
t = LiteratureTools(store, s); reg = t.build_registry()
print("get_summary ->", json.dumps(reg.execute("get_summary", {"doc_id":"不存在"}), ensure_ascii=False)[:200])
print("get_summary 现有 ->", json.dumps(reg.execute("get_summary", {"doc_id": store.connect().execute("select doc_id from summaries limit 1").fetchone()[0]}), ensure_ascii=False)[:160])
m = P.build_round_message(2, 7, "观察")
print("build_round_message 类型:", type(m).__name__)
print(json.dumps(m, ensure_ascii=False)[:300] if not isinstance(m,str) else m[:200])
