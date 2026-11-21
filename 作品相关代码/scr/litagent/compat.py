"""
第三方库兼容垫片，处理不同版本导入名不同的问题
"""
from __future__ import annotations
from typing import Any,Optional

_pdf_module:Any=None
_pdf_module_resolved=False  #初值False，表示还没试过导入

def get_pdf_module()->Optional[Any]:
    """
    返回可用的PyMuPDF模块（'pymupdf'或'fitz'），都不可用则'None'
    结果会被缓存，重复调用不会反复尝试导入
    """
    global _pdf_module,_pdf_module_resolved  #global声明，表示要使用模块级别的变量，而不是局部变量
    if _pdf_module_resolved:   #判断_pdf_module_resolved是否为真
        return _pdf_module   #以前找过直接返回
    for module_name in ("pymupdf", "fitz"):   #先试'pymupdf'，不行再用旧的'fitz'
        try:
            _pdf_module=__import__(module_name)  #用__import__按字符串名字导入模块，把结果存进_pdf_module
            break   #第一个名字成功导入直接跑路
        except ImportError:  #只捕获名字模块不存在，别的错误不进
            continue  #试fitz
    _pdf_module_resolved=True
    return _pdf_module  #成功为模块对象，失败为None

def has_pdf_support()->bool:
    """是否具备 PDF 解析能力，否->降级"""
    return get_pdf_module() is not None   #调用get_pdf_module()，不是None说明有PDF支持
__all__=["get_pdf_module", "has_pdf_support"]