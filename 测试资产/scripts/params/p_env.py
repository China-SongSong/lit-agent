# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, os
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
import litagent.config as cfg

PARAMS = [
 ("LIT_DATA_DIR","data_dir","D:/tmp_data",str),
 ("LIT_DEPS_DIR","deps_dir","D:/tmp_deps",str),
 ("LIT_LLM_API_KEY","llm_api_key","sk-test-000",str),
 ("LIT_LLM_BASE_URL","llm_base_url","https://example.com/v1",str),
 ("LIT_LLM_MODEL","llm_model","test-model",str),
 ("LIT_LLM_TEMPERATURE","llm_temperature","0.75",float),
 ("LIT_LLM_TIMEOUT","llm_timeout","45.5",float),
 ("LIT_LLM_MAX_RETRIES","llm_max_retries","7",int),
 ("LIT_LLM_MAX_CONCURRENCY","llm_max_concurrency","9",int),
 ("LIT_LLM_MAX_INPUT_CHARS","llm_max_input_chars","12345",int),
 ("LIT_CONTACT_EMAIL","contact_email","a@b.com",str),
 ("LIT_HTTP_TIMEOUT","http_timeout","12.5",float),
 ("LIT_HTTP_MAX_RETRIES","http_max_retries","2",int),
 ("LIT_HTTP_BACKOFF_BASE","http_backoff_base","2.5",float),
 ("LIT_OPENALEX_RPS","openalex_rps","3.5",float),
 ("LIT_CROSSREF_RPS","crossref_rps","2.5",float),
 ("LIT_ARXIV_RPS","arxiv_rps","0.5",float),
 ("LIT_S2_RPS","s2_rps","0.25",float),
 ("LIT_S2_API_KEY","s2_api_key","s2-key",str),
 ("LIT_PROXY","requests_proxy","http://127.0.0.1:7890",str),
 ("LIT_BM25_K1","bm25_k1","1.2",float),
 ("LIT_BM25_B","bm25_b","0.6",float),
 ("LIT_FINGERPRINT_N","fingerprint_n","5",int),
 ("LIT_RRF_K","rrf_k","30",int),
 ("LIT_TOP_K","default_top_k","7",int),
 ("LIT_MAX_CHUNK_CHARS","max_chunk_chars","900",int),
 ("LIT_CHUNK_OVERLAP_CHARS","chunk_overlap_chars","50",int),
 ("LIT_AGENT_MAX_ROUNDS","agent_max_rounds","5",int),
 ("LIT_AGENT_OBSERVATION_CHARS","agent_observation_chars","1500",int),
 ("LIT_STREAMLIT_PORT","streamlit_port","9999",int),
]

print("%-32s %-22s %-14s %s" % ("环境变量", "字段", "期望", "结果"))
ok = fail = 0
for env, field, val, typ in PARAMS:
    old = os.environ.get(env)
    os.environ[env] = val
    try:
        s = cfg.build_settings()
        got = getattr(s, field)
        exp = typ(val)
        if typ is str or "DIR" in env or "URL" in env or "PROXY" in env:
            good = str(got).replace("\\","/").endswith(val.replace("\\","/")) or str(got) == val
        else:
            good = abs(float(got) - float(exp)) < 1e-9
    except Exception as e:
        got, good = "EXC: %s" % str(e)[:40], False
    finally:
        if old is None: os.environ.pop(env, None)
        else: os.environ[env] = old
    print("%-32s %-22s %-14s %s %s" % (env, field, val, "OK" if good else "FAIL", "" if good else str(got)[:40]))
    ok += good; fail += (not good)
print("\n环境变量覆盖：%d 项，成功 %d，失败 %d" % (len(PARAMS), ok, fail))

# 类型异常：非法值是否优雅处理
print("\n--- 非法值处理 ---")
for env, field, bad in [("LIT_FINGERPRINT_N","fingerprint_n","abc"),
                        ("LIT_AGENT_MAX_ROUNDS","agent_max_rounds","-3"),
                        ("LIT_MAX_CHUNK_CHARS","max_chunk_chars","0"),
                        ("LIT_LLM_TEMPERATURE","llm_temperature","xyz")]:
    old = os.environ.get(env); os.environ[env] = bad
    try:
        s = cfg.build_settings(); got = getattr(s, field)
        print("  %-24s=%-6s -> %-10s %s" % (env, bad, got, "（回退默认）" if str(got) != bad else ""))
    except Exception as e:
        print("  %-24s=%-6s -> 抛异常 %s" % (env, bad, str(e)[:50]))
    finally:
        if old is None: os.environ.pop(env, None)
        else: os.environ[env] = old
