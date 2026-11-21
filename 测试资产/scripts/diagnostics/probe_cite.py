# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, "C:/Users/Lenovo/Desktop/lit_agent/作品相关代码/scr")
from litagent.cite.gbt7714 import format_gbt7714, format_ris, format_bibtex
from litagent.cite.guji import format_history_note, _circled, format_gbt7714_guji

en = {"id":"d1","title":"Understanding Thermal Runaway in Lithium-Ion Batteries",
      "authors":[{"family":"Liu","given":"Chenchen"},{"family":"Dai","given":"Hai"},
                 {"family":"Wang","given":"Danyang"},{"family":"Ren","given":"Xiaobo"}],
      "year":2024,"venue":"Journal of The Electrochemical Society","venue_type":"journal",
      "volume":"171","issue":"12","pages":"120527","language":"en"}
print("GB/T(en):", format_gbt7714(en))

zh = {"id":"d2","title":"图神经网络推荐中的元学习冷启动方法",
      "authors":[{"family":"张","given":"三"},{"family":"李","given":"四"}],
      "year":2025,"venue":"计算机学报","venue_type":"journal","volume":"48","issue":"3","pages":"1-18","language":"zh"}
print("GB/T(zh):", format_gbt7714(zh))

guji = {"id":"g1","title":"史记","chapter_title":"项羽本纪","chapter_index":"7",
        "authors":[{"family":"司马迁","given":""}],"year":"1959","publisher":"中华书局",
        "publisher_place":"北京","edition":"点校本","language":"zh",
        "extra":{"collator":"点校者"}}
print("\nhistory(first=True) :", format_history_note(guji, page="295", first=True, index_style="circled"))
print("history(first=False):", format_history_note(guji, page="295", first=False, index_style="circled"))
print("\n圈码:", _circled(1), _circled(20), _circled(21), _circled(36), _circled(51))
