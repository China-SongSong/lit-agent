"""稳定哈希工具"""
from __future__ import annotations

import hashlib
import re
from typing import Iterable, List, Sequence

#保留的位数（63位
BITS=63
_MASK=(1<<BITS)-1

def stable_hash64(text: str)->int:
    """接收字符串并返回非负的63位整数哈希值"""
    digest=hashlib.blake2b(text.encode("utf-8"),digest_size=8).digest()  #指定摘要为8字节
    return int.from_bytes(digest,"big")&_MASK

def hash_shingle(tokens: Sequence[str])->int:
    """把一串词元哈希成一个整数指纹，用us分隔符连接词元，避免"ab"+"c"和"a"+"bc"产生同样的指纹"""
    return stable_hash64("\x1f".join(tokens))

def fingerprint_tokens(tokens:Sequence[str],n:int)->List[int]:
    """对词元序列做滑动窗口，返回n-gram指纹的列表，窗口大小为n"""
    if n<=0 or len(tokens)<n:  #窗口大小非正、词元数量不足时返回空列表
        return []
    return [hash_shingle(tokens[i:i+n]) for i in range(len(tokens)-n+1)]

def hamming_distance(a:int,b:int)->int:
    """算两个整数在二进制下有多少位不一样（汉明距离）"""
    return bin((a^b)&_MASK).count("1")  #&_MASK确保只看低63位

def simhash64(tokens:Iterable[str])->int:
    """接受任意可迭代的词元序列，返回一个63位整数SimHash指纹"""
    votes=[0]*BITS  #建一个63个0的票数列表，每一位统计"该位为1"的投票数
    count=0
    for token in tokens:  
        if not token:
            continue
        value=stable_hash64(token)
        count+=1
        for bit in range(BITS):
            if value>>bit&1:
                votes[bit]+=1
            else:
                votes[bit]-=1
    if count==0:
        return 0
    result=0
    for bit in range(BITS):
        if votes[bit]>0:
            result|=1<<bit
    return result&_MASK

#用于SimHash的分词
_TOKEN_RE=re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+")

def simhash_text(text:str,*,sample_chars:int=20000)->int:
    """对文本算 SimHash，'*'之后的参数只能用关键字传入"""
    if not text:  #空字符串或None拦下
        return 0
    tokens=_TOKEN_RE.findall(text[:sample_chars].lower())
    return simhash64(tokens)

def is_near_duplicate(hash_a:int,hash_b:int,*,threshold:int=3)->bool:
    """判断两个指纹是否近似重复，默认汉明距离阈值为3，'threshold'是可用关键字改的距离阈值"""
    if hash_a==0 or hash_b==0:
        return False
    return hamming_distance(hash_a,hash_b)<=threshold



__all__ = [
    "BITS",
    "stable_hash64",
    "hash_shingle",
    "fingerprint_tokens",
    "hamming_distance",
    "simhash64",
    "simhash_text",
    "is_near_duplicate",
]
