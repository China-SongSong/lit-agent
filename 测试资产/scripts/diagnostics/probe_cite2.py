# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, "C:/Users/Lenovo/Desktop/lit_agent/作品相关代码/scr")
from litagent.cite.guji import format_history_note, format_gbt7714_guji, _circled
guji = {"id":"g1","book_title":"史记","chapter_title":"项羽本纪","chapter_index":"7",
        "authors":[{"family":"司马迁","given":""}],"year":"1959","publisher":"中华书局",
        "publisher_place":"北京","edition":"点校本","language":"zh",
        "extra":{"collator":"点校者"}}
print("first=True :", repr(format_history_note(guji, page="295", first=True, index_style="circled")))
print("first=False:", repr(format_history_note(guji, page="295", first=False, index_style="circled")))
print()
g2 = dict(guji); g2["extra"] = {"collator":"点校者","volume_count":"3"}
print("带册数     :", repr(format_history_note(g2, page="295", first=True, index_style="circled")))
print("GB/T 古籍  :", repr(format_gbt7714_guji(guji)))
