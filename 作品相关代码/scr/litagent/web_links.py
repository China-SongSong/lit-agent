"""可分享链接中的问题与溯源参数解析。"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Any,Dict,Mapping,Optional
from urllib.parse import urlencode

_CONTROL_RE=re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
QUESTION_MAX_CHARS=4000
TRACE_MAX_CHARS=20000


def _first(params:Mapping[str,Any],*names:str)->str:
    for name in names:
        value=params.get(name)
        if isinstance(value,(list,tuple)):
            value=value[0] if value else ""
        if value is not None and str(value).strip():
            return str(value)
    return ""


def _clean(value:str,limit:int)->str:
    text=_CONTROL_RE.sub("",str(value or ""))
    text=text.replace("\r\n","\n").replace("\r","\n").strip()
    return text[:limit]


def _truthy(value:str)->bool:
    return str(value or "").strip().lower() in(
        "1","true","yes","on","是","联网"
    )


@dataclass(frozen=True)
class UrlPrefill:
    """从 URL 解析出的问题或溯源预填内容。"""

    mode:str=""
    question:str=""
    quote:str=""
    source_hint:str=""
    allow_online:bool=False
    autorun:bool=False
    autorun_token:str=""
    token:str=""


def parse_url_prefill(params:Mapping[str,Any])->UrlPrefill:
    """解析 ``question``/``trace`` 参数，兼容 ``mode`` 与 ``q``。"""
    mode=_first(params,"mode").lower()
    question=_clean(_first(params,"question"),QUESTION_MAX_CHARS)
    quote=_clean(_first(params,"trace","quote"),TRACE_MAX_CHARS)
    generic=_clean(_first(params,"q"),max(QUESTION_MAX_CHARS,TRACE_MAX_CHARS))
    if mode=="trace":
        quote=quote or generic[:TRACE_MAX_CHARS]
    elif not question:
        question=generic[:QUESTION_MAX_CHARS]
    if quote and not question:
        mode="trace"
    elif question:
        mode="chat"
    else:
        mode=""
    hint=_clean(_first(params,"hint","source_hint"),2000)
    online=_truthy(_first(params,"online","allow_online"))
    autorun=_truthy(_first(params,"autorun","auto","run"))
    autorun_token=str(_first(params,"token") or "")
    payload={
        "mode":mode,"question":question,"quote":quote,
        "source_hint":hint,"allow_online":online,"autorun":autorun,
    }
    token=hashlib.sha256(
        json.dumps(payload,ensure_ascii=False,sort_keys=True).encode("utf-8")
    ).hexdigest()[:16] if mode else ""
    return UrlPrefill(
        mode=mode,question=question,quote=quote,source_hint=hint,
        allow_online=online,autorun=autorun,
        autorun_token=autorun_token,token=token,
    )


def build_prefill_url(
    base_url:str,*,mode:str,text:str,
    source_hint:str="",allow_online:bool=False,
    autorun:bool=False,autorun_token:str="",
)->str:
    """生成可分享的预填或授权自动执行链接。"""
    query={"mode":"trace" if mode=="trace" else "chat","q":str(text or "")}
    if source_hint:
        query["hint"]=str(source_hint)
    if mode=="trace" and allow_online:
        query["online"]="1"
    if autorun:
        query["autorun"]="1"
    if autorun and autorun_token:
        query["token"]=str(autorun_token)
    separator="&" if "?" in base_url else "?"
    return str(base_url).rstrip("?&")+separator+urlencode(query)


def autorun_allowed(prefill:UrlPrefill,configured_token:str="")->bool:
    """配置令牌时必须匹配；未配置时允许本地系统直接接入。"""
    if not prefill.autorun or prefill.mode!="chat" or not prefill.question:
        return False
    expected=str(configured_token or "")
    if not expected:
        return True
    return hmac.compare_digest(prefill.autorun_token,expected)


__all__=[
    "QUESTION_MAX_CHARS","TRACE_MAX_CHARS","UrlPrefill",
    "autorun_allowed","build_prefill_url","parse_url_prefill",
]
