# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, "C:/Users/Lenovo/Desktop/lit_agent/作品相关代码/scr")
from litagent.llm.client import extract_json
Q = chr(34); BS = chr(92)
esc = BS + Q
raw_text = '{"a": "他说 ' + esc + '括号{不是结构}' + esc + ' 结束"}'
print("输入:", raw_text)
got = extract_json(raw_text)
print("输出:", got)
expect = "他说 " + Q + "括号{不是结构}" + Q + " 结束"
print("期望:", expect)
print("一致:", got == {"a": expect})

# 另测：字符串内含未转义括号（合法 JSON）
raw2 = '{"a": "值 {含括号} 但不嵌套"}'
print("\n输入:", raw2)
print("输出:", extract_json(raw2))
print("一致:", extract_json(raw2) == {"a": "值 {含括号} 但不嵌套"})
