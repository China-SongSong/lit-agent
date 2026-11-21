"""正文清洗、去重和分块。

pipeline 串联 PDF 抽取、文本规范化、去重与分块，最后写出 Document
和可检索的 Chunk。
"""
from __future__ import annotations  #从 `__future__` 导入 `annotations`，让类型注解延迟求值，旧版本 Python 也不会报错。

__all__=[]  #`__all__` 赋值为空列表，表示本包不导出任何名字；若写成有内容的列表才是导出公共名字。
