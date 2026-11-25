"""ReAct Agent 主循环。

模型每轮只能调用一个工具或直接作答。工具结果会写回对话，下一轮再
根据新信息继续，直到得到答复或达到轮数上限。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from litagent.agent.prompts import (
    PROMPT_VERSION,
    build_chat_messages,
    build_round_message,
    build_system_prompt,
)
from litagent.agent.tools import LiteratureTools, ToolRegistry
from litagent.config import Settings
from litagent.config import settings as default_settings
from litagent.index.store import Store
from litagent.llm.client import LLMClient, LLMError


@dataclass
class AgentStep:
    """Agent的一步（一次思考+一次动作）"""
    round_index:int #第几轮
    thought:str=""  #模型这一步的思考
    tool:str=""  #本轮要调用的工具，没调用就是空串
    arguments:Dict[str,Any]=field(default_factory=dict)  #工具参数
    observation:str=""  #工具返回的结果，已按长度截断
    error:str=""  #这一步出错时的原因

    def to_dict(self)->Dict[str,Any]:
        """转成可以序列化的字典，给界面和接口用"""
        return{
            "round":self.round_index,
            "thought":self.thought,
            "tool":self.tool,
            "arguments":self.arguments,
            "observation":self.observation[:1200],
            "error":self.error,
        }

@dataclass
class AgentResult:
    """Agent一次完整问答的结果"""
    question:str=""
    answer:str=""
    steps:List[AgentStep]=field(default_factory=list)
    rounds:int=0
    stopped_reason:str="final_answer"
    error:str=""
    elapsed_seconds:float=0.0
    prompt_tokens:int=0
    completion_tokens:int=0
    online_candidates:List[Dict[str,Any]]=field(default_factory=list)  #本轮联网检索的候选（含真实性核验结果），供界面渲染结构化区块

    @property
    def ok(self)->bool:
        """是否拿到了答复并且没有出错"""
        return bool(self.answer) and not self.error

    def to_dict(self)->Dict[str,Any]:
        """转成可以序列化的字典"""
        return{
            "question":self.question,
            "answer":self.answer,
            "rounds":self.rounds,
            "stopped_reason":self.stopped_reason,
            "error":self.error,
            "elapsed_seconds":round(self.elapsed_seconds,2),
            "steps":[s.to_dict() for s in self.steps],
        }

    def transcript(self)->str:
        """把每一步整理成可读文本，方便看清Agent是怎么一步步得出结论的"""
        lines:List[str]=[]
        for step in self.steps:
            lines.append("─"*60)
            lines.append("第 %d 轮  thought: %s"%(step.round_index,step.thought))
            if step.tool:
                lines.append("        action : %s(%s)"
                             %(step.tool,json.dumps(step.arguments,ensure_ascii=False)))
            if step.error:
                lines.append("        error  : %s"%step.error)
            if step.observation:
                lines.append("        result : %s"%step.observation[:600])
        return "\n".join(lines)

class LiteratureAgent:
    """文献检索 Agent"""
    def __init__(self,store:Store,settings:Optional[Settings]=None,
                 *,client:Optional[LLMClient]=None,
                 tools:Optional[LiteratureTools]=None,
                 registry:Optional[ToolRegistry]=None)->None:
        """准备工具注册表、系统提示词和模型客户端
        后面三个参数是留给测试的注入口，不传就按当前配置自己建
        """
        self.store=store
        self.settings=settings or default_settings

        self.tools=tools or LiteratureTools(store,self.settings)
        self.registry=registry or self.tools.build_registry()
        #允许注入客户端：可复用已配置好的实例
        self.client=client if client is not None else self._build_client()
        self.system_prompt=build_system_prompt(
            self.registry,max_rounds=self.settings.agent_max_rounds
        )

    def _build_client(self)->LLMClient:
        """按配置建一个模型客户端，没配Key的话会在这一步直接报错"""
        self.settings.require_llm()
        return LLMClient(self.settings)

    def run(self,question:str,*,
            max_rounds:Optional[int]=None,
            history:Optional[Sequence[Dict[str,str]]]=None,
            on_step:Optional[Callable[[AgentStep],None]]=None)->AgentResult:
        """回答一个问题，返回AgentResult
        max_rounds不传就用配置里的上限：history是之前几轮的对话，用于多轮追问；
        on_step每完成一轮回调一次，界面靠它显示当前在调用哪个工具
        """
        started=time.monotonic()  #用单调时钟计时，不受系统改时间影响
        limit=max(1,int(max_rounds or self.settings.agent_max_rounds))  #轮数上限：调用方指定优先，至少1轮
        result=AgentResult(question=question)

        #把系统提示词、历史对话和本轮问题拼成消息列表
        messages=build_chat_messages(self.system_prompt,list(history or[]),question)
        #第一轮的开场提示，顺便把轮数上限告诉模型
        messages.append(build_round_message(0,limit))

        seen_calls:Dict[str,int]={}  #记录同一工具加同一参数出现过几次，用来防死循环
        bad_outputs=0                #模型连续输出不合规 JSON 的次数

        for round_index in range(1,limit+1):  #最多跑 limit 轮的循环
            result.rounds=round_index  #当前跑到第几轮
            step=AgentStep(round_index=round_index)  #本轮的动作记录

            #让模型决策：要么给出工具调用，要么给出最终答复
            try:
                payload=self.client.chat_json(messages,retries=1)  #解析不出JSON时，客户端内部会自己再试一次
            except LLMError as exc:
                step.error="模型调用失败：%s"%str(exc)[:200]
                result.steps.append(step)
                result.error=step.error
                result.stopped_reason="llm_error"
                break

            if payload is None or not isinstance(payload,dict):  #模型偶尔会截断、加前言或写成markdown
                #让它再来一次，通常第二次就能正常输出
                bad_outputs+=1  #连续不合规才计数，中间只要正常一轮就会清零
                step.error=("模型没有返回可解析的 JSON 对象"
                              if payload is None else
                              "模型返回的不是 JSON 对象：%s"%str(payload)[:120])
                result.steps.append(step)
                if bad_outputs>=2:
                    result.error=step.error
                    result.stopped_reason="bad_output"
                    break
                messages.append({  #追加一条纠错指令，让它按协议重来
                    "role":"user",
                    "content":("你刚才的输出不是合法的JSON对象。请严格遵守协议，"
                                "只输出一个 JSON 对象，不要有任何其它文字：\n"
                                "{\"thought\": \"...\", \"action\": {\"tool\": \"...\", "
                                "\"args\": {...}}}\n"
                                "或\n"
                                "{\"thought\": \"...\", \"final_answer\": \"...\"}"),
                })
                continue

            bad_outputs=0  #这一轮输出正常，计数清零
            step.thought=str(payload.get("thought") or payload.get("thinking") or "")  #思考内容，两个键名都兼容

            #最终答复
            final=payload.get("final_answer") or payload.get("answer")  #最终答复的两种写法
            if final and not payload.get("action"):  #有答复、且没要求继续调工具，才算收尾
                result.answer=str(final).strip()
                result.steps.append(step)
                result.stopped_reason="final_answer"
                if on_step:
                    on_step(step)
                break

            #工具调用
            action=payload.get("action")  #走到这里说明不是最终答复，那就应该是一次工具调用
            if not isinstance(action,dict):  #两边都没有，属于输出不合协议
                messages.append({"role":"assistant",
                                 "content":json.dumps(payload,ensure_ascii=False)[:800]})
                messages.append({
                    "role":"user",
                    "content":("你的输出不符合协议：既没有 action 也没有 final_answer。"
                                "请只输出 {\"thought\":..., \"action\":{\"tool\":...,"
                                "\"args\":{...}}} 或 {\"thought\":..., \"final_answer\":...}"),
                })
                step.error="输出缺少 action / final_answer"
                result.steps.append(step)
                continue

            tool_name=str(action.get("tool") or action.get("name") or "").strip()  #工具名的两种写法
            arguments=action.get("args") or action.get("arguments") or{}  #参数的两种写法
            step.tool=tool_name  #记进本轮动作
            step.arguments=arguments if isinstance(arguments,dict) else{}  #参数不是字典就当作没传

            #防死循环：同样的调用出现第三次就拦下来
            signature="%s:%s"%(tool_name,json.dumps(step.arguments,
                                                         sort_keys=True,ensure_ascii=False))  #参数排序后再序列化，保证同一组参数得到同一个签名
            seen_calls[signature]=seen_calls.get(signature,0)+1  #这次调用记一笔
            if seen_calls[signature]>2:  #同一调用第3次出现就打住，避免原地打转
                observation=json.dumps({
                    "error":"你已经用完全相同的参数调用过 %s 两次了，结果不会变。"
                             "请换一个检索词、换一个工具，或者直接给出 final_answer。"
                             %tool_name
                },ensure_ascii=False)
                step.observation=observation
                messages.append({"role":"assistant",
                                 "content":json.dumps(payload,ensure_ascii=False)[:800]})
                messages.append(build_round_message(round_index,limit,observation))
                result.steps.append(step)
                if on_step:
                    on_step(step)
                continue

            #执行工具并把结果带回去
            output=self.registry.execute(tool_name,step.arguments)  #异常已在工具层转成错误对象，这里不会抛
            observation=self._format_observation(output)  #序列化并按配置长度截断
            step.observation=observation  #记下观察结果
            if isinstance(output,dict) and output.get("error"):  #工具自己报的错也记进这一步
                step.error=str(output["error"])[:200]

            #联网检索候选结构化透传：界面据此渲染「本轮联网候选」区块（含真实性核验徽章）
            if tool_name=="fetch_online" and isinstance(output,dict):
                papers=output.get("papers")
                if isinstance(papers,list) and papers:
                    result.online_candidates=[
                        {k:p.get(k) for k in("title","authors","year","venue","doi","url","verification","citation") if k in p}
                        for p in papers if isinstance(p,dict)
                    ]

            result.steps.append(step)  #收进结果
            if on_step:
                on_step(step)  #回调一次，界面据此实时显示进度

            messages.append({"role":"assistant",
                             "content":json.dumps(payload,ensure_ascii=False)[:1200]})
            messages.append(build_round_message(round_index,limit,observation))  #再把工具结果和剩余轮数喂回去

        #轮数耗尽：强制收尾
        if not result.answer and not result.error:  #跑满轮数还没有答复，也没有硬错误
            result.stopped_reason="max_rounds"
            result.answer=self._force_answer(messages)  #再问一次，要求基于已有信息直接作答
            if not result.answer:  #还是拿不到就给一段兜底说明
                result.answer=("抱歉，我在 %d 轮内没能收集到足够信息回答这个问题。"
                                 "可以试试换个更具体的检索词，或者先用 "
                                 "`cli.py fetch` 扩充本地语料。"%limit)
                result.error="达到最大轮数仍未给出答复"

        usage=self.client.usage  #客户端累计的用量
        result.prompt_tokens=usage.prompt_tokens  #输入token
        result.completion_tokens=usage.completion_tokens  #输出token
        result.elapsed_seconds=time.monotonic()-started  #总耗时
        return result

    def _format_observation(self,output:Any)->str:
        """把工具结果序列化成文本，并截断到配置的长度上限"""
        try:
            text=json.dumps(output,ensure_ascii=False,default=str)  #default=str：遇到不能序列化的对象就转成字符串
        except(TypeError,ValueError):
            text=str(output)
        #必须截断：一次检索可能返回几千字，几轮之后上下文就装不下了
        limit=self.settings.agent_observation_chars
        if len(text)>limit:
            text=text[:limit]+"……（结果过长已截断）"
        return text

    def _force_answer(self,messages:List[Dict[str,str]])->str:
        """轮数耗尽时，要求模型基于已有信息直接作答。"""
        #在原有消息后面追加一条指令，让模型收口，而不是继续调工具
        messages=list(messages)+[{
            "role":"user",
            "content":("已达到最大工具调用轮数。请**不要再调用工具**，"
                        "直接基于上面已经获得的信息输出 "
                        "{\"thought\": \"...\", \"final_answer\": \"...\"}。"
                        "如果信息确实不足，就在答复里如实说明还缺什么。"),
        }]
        try:
            payload=self.client.chat_json(messages,retries=1)
        except LLMError:
            return ""
        if isinstance(payload,dict):
            final=payload.get("final_answer") or payload.get("answer")
            if final:
                return str(final).strip()
        return ""

    def ask(self,question:str,**kwargs:Any)->str:
        """只要答复文本的简版接口"""
        return self.run(question,**kwargs).answer

    def chat(self,question:str,
             history:Optional[Sequence[Dict[str,str]]]=None,
             **kwargs:Any)->AgentResult:
        """对话式接口，可携带历史轮次"""
        return self.run(question,history=history,**kwargs)

    def close(self)->None:
        """关闭底层的模型客户端，关不掉也不影响已经拿到的结果"""
        try:
            self.client.close()
        except Exception:  # noqa: BLE001
            pass

__all__=["LiteratureAgent","AgentResult","AgentStep","PROMPT_VERSION"]
