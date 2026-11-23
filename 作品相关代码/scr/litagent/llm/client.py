"""大模型客户端（OpenAI 兼容，默认 DeepSeek）。"""
#模块依赖与类型
from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Sequence

from litagent.config import Settings
from litagent.config import settings as default_settings

#可选的第三方依赖
try:
    import requests
except ImportError:
    requests=None  #type: ignore[assignment]

Message=Dict[str,str]

#异常类型
class LLMError(RuntimeError):
    """大模型调用失败。"""

    def __init__(self,message:str,*,status:Optional[int]=None,  #status和retryable用关键字限定
                 retryable:bool=False)->None:
        super().__init__(message)
        self.status=status
        self.retryable=retryable  #默认不可重试：宁可少试一次

#JSON 提取
_FENCE_RE=re.compile(r"```(?:json|JSON)?\s*(.*?)```",re.S)

def _balanced_json(text:str)->Optional[str]:
    """从文本里抠出第一个括号平衡的 JSON 对象或数组。"""
    start=-1
    for index,char in enumerate(text):
        if char in "{[":
            start=index
            break
    if start<0:  #第一个左括号就决定了这次要配哪种闭括号
        return None

    opening=text[start]
    closing="}" if opening=="{" else "]"
    depth=0
    in_string=False  #in_string为真表示当前位置在字符串字面量里
    escaped=False

    for index in range(start,len(text)):
        char=text[index]  #进入字符串后单独走一条通道
        if in_string:
            if escaped:
                escaped=False
            elif char=="\\":  #裸引号才是真正的结束
                escaped=True
            elif char=='"':
                in_string=False
            continue
        if char=='"':  #见到裸引号就进入字符串状态
            in_string=True
        elif char==opening:
            depth+=1
        elif char==closing:  #闭括号先减深度再判断
            depth-=1
            if depth==0:
                return text[start:index+1]
    return None  #扫完都没配平，说明括号是残缺的

def extract_json(text:str)->Optional[Any]:
    """从模型输出里尽力提取 JSON。失败返回 'None'。"""
    if not text:  #空串直接返回
        return None
    candidates:List[str]=[]

    stripped=text.strip()  #strip先修掉模型爱加的首尾空行和空白
    candidates.append(stripped)

    for match in _FENCE_RE.finditer(stripped):
        candidates.append(match.group(1).strip())  #只取围栏内的正文

    balanced=_balanced_json(stripped)
    if balanced:
        candidates.append(balanced)

    for candidate in candidates:
        if not candidate:  #候选串之间可能互相重复，空的直接丢弃
            continue
        try:
            return json.loads(candidate)
        except(ValueError,TypeError):  #TypeError也要接
            pass
        repaired=re.sub(r",\s*([}\]])",r"\1",candidate)
        if repaired!=candidate:  #没改动说明压根没有尾随逗号
            try:
                return json.loads(repaired)
            except(ValueError,TypeError):
                pass
    return None

#用量统计
@dataclass
class LLMUsage:
    """一次调用的用量统计，用于成本核算。"""

    prompt_tokens:int=0  #三个计数分开记
    completion_tokens:int=0
    total_tokens:int=0
    calls:int=0
    cached_calls:int=0

    def add(self,other:"LLMUsage")->None:  #other也传LLMUsage
        self.prompt_tokens+=other.prompt_tokens
        self.completion_tokens+=other.completion_tokens
        self.total_tokens+=other.total_tokens
        self.calls+=other.calls
        self.cached_calls+=other.cached_calls

    def summary(self)->str:
        return("调用 %d 次（缓存命中 %d 次），输入 %d token，输出 %d token，"  #拼成一行给日志和cli.py用
                "合计 %d token"%(self.calls,self.cached_calls,
                                   self.prompt_tokens,self.completion_tokens,
                                   self.total_tokens))

#客户端主体
class LLMClient:
    """OpenAI 兼容的对话客户端。"""

    RETRY_STATUS=frozenset({408,429,500,502,503,504,522,524})  #只重试这些状态码：限流、超时、网关抖动

    def __init__(self,settings:Optional[Settings]=None,*,
                 api_key:Optional[str]=None,
                 base_url:Optional[str]=None,
                 model:Optional[str]=None,
                 store:Optional[Any]=None)->None:
        if requests is None:  #pragma: no cover
            raise LLMError("未安装 requests，无法调用大模型")
        self.settings=settings or default_settings
        self.api_key=api_key if api_key is not None else self.settings.llm_api_key  #用is not None判断
        self.base_url=(base_url or self.settings.llm_base_url).rstrip("/")
        self.model=model or self.settings.llm_model
        self.store=store
        self.usage=LLMUsage()  #整个客户端共用一个用量对象

        if not self.api_key:
            raise LLMError(
                "未配置大模型 API Key。\n"
                "请设置环境变量 LIT_LLM_API_KEY（或 DEEPSEEK_API_KEY），"  #DEEPSEEK_API_KEY是兼容写法
                "例如在项目根目录的 .env 中写入：\n"
                "    LIT_LLM_API_KEY=sk-xxxxxxxx\n"
                "参考 .env.example。申请地址 https://platform.deepseek.com/\n"
                "提示：抓取/清洗/索引/检索/引用等功能不依赖大模型，不受影响。"  #说清哪些功能不受影响
            )

        self._session=requests.Session()
        self._session.headers.update({
            "Authorization":"Bearer "+self.api_key,  #Bearer前面那个空格不能省
            "Content-Type":"application/json",
        })

    #底层请求
    #端点拼接
    def _endpoint(self)->str:
        """拼接 chat completions 端点。"""
        base=self.base_url
        if base.endswith("/chat/completions"):  #用户已经把完整端点写全了
            return base
        return base+"/chat/completions"

    #带重试的请求
    def _post(self,payload:Dict[str,Any],*,stream:bool=False)->Any:
        url=self._endpoint()
        last_error:Optional[Exception]=None

        #重试循环
        for attempt in range(self.settings.llm_max_retries+1):
            try:
                response=self._session.post(
                    url,data=json.dumps(payload,ensure_ascii=False),  #ensure_ascii=False保留中文原样
                    timeout=self.settings.llm_timeout,stream=stream,
                )
            except Exception as exc:
                last_error=exc
                if attempt>=self.settings.llm_max_retries:
                    break
                time.sleep(min(2.0**attempt+random.uniform(0,0.5),30.0))  #指数退避加抖动封顶 30 秒
                continue

            if response.status_code in self.RETRY_STATUS:  #这几类状态码是等一会儿再来的意思
                last_error=LLMError(
                    "HTTP %d: %s"%(response.status_code,response.text[:200]),
                    status=response.status_code,retryable=True)
                if attempt>=self.settings.llm_max_retries:
                    response.close()  #重试次数已用尽，先把连接还回去再跳出循环
                    break
                retry_after=response.headers.get("Retry-After")
                try:
                    delay=float(retry_after) if retry_after else 0.0  #没有Retry-After头就取 0
                except(TypeError,ValueError):
                    delay=0.0
                response.close()
                time.sleep(min(delay or(2.0**attempt+random.uniform(0,0.5)),30.0))  #delay or ...：服务端说了就听它的
                continue

            if response.status_code>=400:  #400 段里除限流外都是重试也没用的硬错误
                text=response.text[:400]
                response.close()
                raise LLMError("HTTP %d: %s"%(response.status_code,text),
                               status=response.status_code)

            #成功收尾
            if stream:
                return response
            try:
                data=response.json()  #非流式要一次性读完整个 JSON，这里失败没有兜底
            finally:
                response.close()
            return data

        #重试次数用尽
        raise LLMError("大模型调用失败（已重试 %d 次）：%s"
                       %(self.settings.llm_max_retries,last_error),
                       retryable=True)

    #对话接口
    #普通对话
    def chat(self,messages:Sequence[Message],*,
             temperature:Optional[float]=None,
             max_tokens:Optional[int]=None,
             json_mode:bool=False,
             cache_key:Optional[str]=None)->str:
        """发起一次对话，返回回复文本。"""
        #先查缓存
        if cache_key and self.store is not None:
            cached=self.store.cache_get(cache_key,self.model)
            if cached is not None:
                self.usage.cached_calls+=1  #命中缓存只记账不加调用费
                return cached

        payload:Dict[str,Any]={
            "model":self.model,
            "messages":list(messages),
            "temperature":(self.settings.llm_temperature  #温度没传就用配置里的默认值
                            if temperature is None else temperature),
            "stream":False,
        }
        if max_tokens:
            payload["max_tokens"]=max_tokens  #if max_tokens顺带挡掉 0
        if json_mode:
            payload["response_format"]={"type":"json_object"}

        #发出去并记账
        data=self._post(payload)
        self._record_usage(data)

        #取回复正文
        try:
            content=data["choices"][0]["message"]["content"]
        except(KeyError,IndexError,TypeError) as exc:
            raise LLMError("响应结构异常：%s"%str(data)[:300]) from exc  #只留前 300 字符

        text=content or ""
        if cache_key and self.store is not None and text:  #空回复不写缓存
            self.store.cache_put(cache_key,text,self.model)
        return text

    #要 JSON 的对话
    def chat_json(self,messages:Sequence[Message],*,retries:int=2,
                  **kwargs:Any)->Optional[Any]:
        """要求模型输出 JSON，并解析成 Python 对象。"""
        #不满意就让模型自己改
        conversation=list(messages)
        for attempt in range(retries+1):
            text=self.chat(conversation,json_mode=True,**kwargs)
            parsed=extract_json(text)
            if parsed is not None:
                return parsed
            if attempt<retries:  #留一次机会给模型自己改这条路径
                conversation=list(messages)+[
                    {"role":"assistant","content":text[:800]},
                    {"role":"user",
                     "content":"你上面的回复不是合法 JSON。请只输出一个 JSON 对象，"  #这条指令写得比系统提示词更硬
                                "不要包含任何解释文字、不要使用 markdown 代码块。"},
                ]
        return None

    #流式对话
    def chat_stream(self,messages:Sequence[Message],*,
                    temperature:Optional[float]=None,
                    max_tokens:Optional[int]=None)->Iterator[str]:
        """流式对话，逐块产出增量文本（供 SSE 接口用）。"""
        payload:Dict[str,Any]={
            "model":self.model,
            "messages":list(messages),
            "temperature":(self.settings.llm_temperature
                            if temperature is None else temperature),
            "stream":True,
        }
        if max_tokens:  #本次调用不带上限就沿用服务端默认值
            payload["max_tokens"]=max_tokens

        response=self._post(payload,stream=True)  #stream=True让_post直接把响应交出来
        try:
            for raw_line in response.iter_lines(decode_unicode=False):
                if not raw_line:
                    continue
                line=raw_line.decode("utf-8",errors="replace").strip()  #errors=replace兜住半截 UTF-8
                if not line.startswith("data:"):
                    continue
                chunk=line[5:].strip()
                if chunk=="[DONE]":  #[DONE]是结束标记，不是 JSON
                    break
                try:
                    event=json.loads(chunk)
                except ValueError:
                    continue
                choices=event.get("choices") or[]  #or []兜住choices为None的情况
                if not choices:
                    continue
                delta=(choices[0].get("delta") or{}).get("content")
                if delta:  #有些块只带角色或只带结束原因
                    yield delta
        finally:
            response.close()

    #用量统计
    #累加 token
    def _record_usage(self,data:Dict[str,Any])->None:
        self.usage.calls+=1
        usage=(data or{}).get("usage") or{}
        self.usage.prompt_tokens+=int(usage.get("prompt_tokens") or 0)  #int加or 0双保险
        self.usage.completion_tokens+=int(usage.get("completion_tokens") or 0)
        self.usage.total_tokens+=int(usage.get("total_tokens") or 0)

    #体检与收尾
    def probe(self)->Dict[str,Any]:
        """连通性探测，供 'cli.py doctor' 使用。"""
        started=time.monotonic()
        try:
            text=self.chat(
                [{"role":"user","content":"回复两个字：就绪"}],
                temperature=0.0,max_tokens=16,
            )
        except Exception as exc:
            return{"ok":False,"model":self.model,
                    "error":"%s: %s"%(type(exc).__name__,str(exc)[:200])}  #只留前 200 字符
        return{
            "ok":bool(text.strip()),
            "model":self.model,
            "reply":text.strip()[:40],
            "elapsed_seconds":round(time.monotonic()-started,2),  #保留两位小数，读数够用又不会占太宽
        }

    def close(self)->None:
        try:  #关闭失败也不往上抛
            self._session.close()
        except Exception:
            pass

#对外导出的名字
__all__=["LLMClient","LLMError","LLMUsage","extract_json","Message"]
