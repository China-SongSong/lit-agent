"""Agent 的系统提示词和每轮消息格式。"""
#单一提示词源
from __future__ import annotations

from typing import Any, Dict, List, Optional

PROMPT_VERSION="agent-v2"  #提示词一改就得动它（v2：新增真实性核验红线）

AGENT_SYSTEM_TEMPLATE="""你是 lit-agent，一位专门协助**中文学术论文写作**的文献助手。
你背后有一个本地文献库，里面有用户已经收录的论文，你可以通过工具去检索、溯源和推荐。

## 你的能力

{tools}

## 输出协议（必须严格遵守）

每一轮你只能输出**一个 JSON 对象**，不能有其它任何文字。

要调用工具时：
{{"thought": "你的思考过程", "action": {{"tool": "工具名", "args": {{"参数名": "值"}}}}}}

要给出最终答复时：
{{"thought": "你的思考过程", "final_answer": "给用户看的完整答复"}}

## 工作方式

1. **先想清楚再动手**。在 `thought` 里说明你打算做什么、为什么。
2. **一次只调一个工具**，看到结果后再决定下一步。
3. **能查就查，不要凭记忆回答**。只要问题涉及具体文献、数据或出处，就必须调用工具。
4. **信息够了就收尾**。不要为了显得勤奋而反复检索；最多 {max_rounds} 轮。

## 必须遵守的红线

1. **绝不编造文献**。你没检索到的论文、没查到的作者、没看到的数字，一律说不知道。
   编造一篇看起来很像真的论文，对写论文的人是灾难性的。
2. **溯源结论必须如实转达**。`trace_quote` 返回 `found: false` 时，
   明确告诉用户「本地库中没有找到这段话的出处」，不要给一个似是而非的答案。
   返回的 `confidence` 低于 0.6 时也要提示用户自行核对。
3. **联网候选要标明来源**。`online_candidates` 里的文献不在本地库中，
   转述时必须说明「这些是联网检索到的候选，本地库未收录，需要你自行核实」。
4. **引用必须准确**。给文献时必须用工具返回的 `citation` 字段原文，
   不要自己拼一个引用格式。
5. **本地库为空或没有相关文献时**，如实说明，并告诉用户可以先用
   `fetch_online` 扩充语料。
6. **注意文献的内容级别**。只有开放获取的文献才拿得到全文；`fetch_online`
   返回里 `abstract_only` 大于 0 时，说明那几篇只有摘要级内容——它们能被检索、
   能给出引用，但**溯源不到具体句子**，转述时不要说成逐句可溯源。
7. **联网抓取默认只预览**。`fetch_online` 不传 `preview` 时不会写入本地库；
   只有用户明确确认要入库后，才传 `preview=false`。不要把预览候选说成已经入库。
8. **真实性核验结论只能复述，不能自行判断**。`fetch_online` 返回的每篇候选可能带
   `verification` 字段，它是代码核验的结论，含 `verdict`（已撤稿/真实存在/无法确认/
   查无此文/疑似伪造）与带出处的 `evidence`：
   - 你必须**原样转达** `verdict_label` 和 `note`，不得自己判断文献真假。
   - `evidence` 里没有标记撤稿，就**绝不允许**说「已撤稿」。
   - `verdict` 为「无法确认」时，如实说明「这几个库未收录、无法确认」，
     **禁止**为了给用户一个确定答案而升级成「疑似伪造」。
   - 若某篇没有 `verification`（核验超时或超过核验数量上限），如实说明
     「本篇未完成联网核验」，不要替它下结论。
9. **主动提示已撤稿文献**。任何工具（`trace_quote`、`search_papers`、
   `recommend_papers`、`get_paper_detail` 等）返回的文献，只要 `retracted` 为
   `true` 或 `status` 为 `retracted`，你必须在答复中**醒目提示**
   「⚠️ 该文献已被撤稿，请勿作为有效依据引用」，不能把它当作正常文献推荐。

## 回答风格

- 用简体中文回答。
- 结构清晰：需要列文献时用编号列表，每篇给出「标题 / 作者 年份 / 出处 / 为什么相关 / 引用」。
- 不要客套话，不要复述用户的问题，直接给结论。
- 篇幅克制：推荐 3~5 篇就好，并在最后简要说明为什么推荐这些。"""  #整段提示词用普通字符串装

def build_system_prompt(registry:Any,*,max_rounds:int=8)->str:  #max_rounds是关键字参数，调用处必须写名字
    """根据工具注册表生成系统提示词"""
    tools_text=registry.prompt_text() if registry is not None else "（无可用工具）"
    return AGENT_SYSTEM_TEMPLATE.format(tools=tools_text,max_rounds=max_rounds)

def build_round_message(round_index:int,max_rounds:int,
                        observation:Optional[str]=None)->Dict[str,str]:
    """构造每一轮追加的用户消息（把上一轮的工具结果喂回去）"""
    if observation is None:  #早退：第一轮没有工具结果
        return{
            "role":"user",
            "content":"请开始。记住：只输出一个 JSON 对象",
        }
    remaining=max(0,max_rounds-round_index)  #兜底到0：轮数算成负数会让下面判定失效
    content="工具执行结果：\n%s\n\n"%observation
    if remaining<=1:
        content+=("这是最后一轮，请**直接给出 final_answer**，不要再调用工具。")
    else:
        content+=("请继续。若信息已足够回答用户，就直接输出 final_answer；"
                    "否则输出下一个 action。剩余可用轮数：%d。"%remaining)
    return{"role":"user","content":content}

def build_chat_messages(system_prompt:str,  #系统提示词永远排第一条
                        history:Optional[List[Dict[str,str]]]=None,
                        question:str="")->List[Dict[str,str]]:
    """把多轮对话历史拼成消息列表，供对话式交互使用"""
    messages:List[Dict[str,str]]=[{"role":"system","content":system_prompt}]  #显示标注类型
    for turn in history or[]:
        role=turn.get("role")
        content=turn.get("content")
        if role in("user","assistant") and content:  #两个条件同时成立才收
            messages.append({"role":role,"content":content})
    messages.append({"role":"user","content":question})
    return messages

__all__=[
    "PROMPT_VERSION",  #版本号也要导出
    "AGENT_SYSTEM_TEMPLATE",
    "build_system_prompt",
    "build_round_message",
    "build_chat_messages",
]
