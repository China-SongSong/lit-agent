# -*- coding: utf-8 -*-
import sys, inspect
sys.path.insert(0, "C:/Users/Lenovo/Desktop/lit_agent/作品相关代码/scr")
from litagent.llm.client import LLMClient
from litagent.cite.gbt7714 import format_ris, format_gbt7714
print("chat:", inspect.signature(LLMClient.chat))
print("chat_json:", inspect.signature(LLMClient.chat_json))
journal = {"id":"j","title":"Understanding Thermal Runaway","authors":[{"family":"Liu","given":"Chenchen"}],
           "year":2024,"venue":"J. Test","venue_type":"journal","volume":"171","issue":"12","pages":"120527","language":"en"}
print("\n--- RIS ---")
print(format_ris(journal))
four = {"id":"z4","title":"四作者","authors":[{"family":"张","given":"三"},{"family":"李","given":"四"},
        {"family":"王","given":"五"},{"family":"赵","given":"六"}],
        "year":2024,"venue":"测试学报","venue_type":"journal","volume":"1","issue":"1","pages":"1-9","language":"zh"}
print("\n四作者 GB/T:", format_gbt7714(four))
