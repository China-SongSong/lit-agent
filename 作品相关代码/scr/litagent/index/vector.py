"""本地神经语义向量、持久化检索与文献级聚合。

默认后端是 quantized ONNX 多语 MiniLM，使用 SentencePiece 分词、均值池化
和 L2 归一化。`local-hash-v1` 仅保留为显式配置的轻量后备。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import unicodedata
from array import array
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any,Dict,Iterable,List,Optional,Sequence

from litagent.index.lexical import tokenize

HASH_MODEL="local-hash-v1"
NEURAL_MODEL="paraphrase-multilingual-MiniLM-L12-v2-int8"
VECTOR_MODEL=NEURAL_MODEL
_CJK_RUN_RE=re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+")
_LATIN_RE=re.compile(r"[a-z0-9]+")


def _feature(feature:str,dimension:int)->tuple[int,int]:
    digest=hashlib.blake2b(
        feature.encode("utf-8"),digest_size=8
    ).digest()
    value=int.from_bytes(digest,"big")
    return value%dimension,1 if value&1 else -1


def embed_text(text:str,*,dimension:int=384)->array:
    """把文本编码为 L2 归一化的稠密 float32 向量。"""
    vector=array("f",[0.0])*max(32,int(dimension))
    normalized=unicodedata.normalize("NFKC",text or "").lower()
    if not normalized.strip():
        return vector

    features:List[tuple[str,float]]=[]
    words=tokenize(normalized)
    for word in words:
        features.append(("w:"+word,1.0))
    for left,right in zip(words,words[1:]):
        features.append(("b:%s:%s"%(left,right),1.15))

    for run in _CJK_RUN_RE.findall(normalized):
        for size,weight in((2,0.7),(3,0.55)):
            for start in range(max(0,len(run)-size+1)):
                features.append(("c:"+run[start:start+size],weight))
    for token in _LATIN_RE.findall(normalized):
        if len(token)<5:
            continue
        for size,weight in((4,0.25),(5,0.2)):
            for start in range(max(0,len(token)-size+1)):
                features.append(("s:"+token[start:start+size],weight))

    for feature,weight in features:
        index,sign=_feature(feature,len(vector))
        vector[index]+=sign*weight
    norm=math.sqrt(sum(value*value for value in vector))
    if norm:
        for index in range(len(vector)):
            vector[index]/=norm
    return vector


def encode_vector(text:str,*,dimension:int=384)->bytes:
    return embed_text(text,dimension=dimension).tobytes()


def decode_vector(blob:bytes)->array:
    vector=array("f")
    vector.frombytes(blob)
    return vector


def cosine(left:Sequence[float],right:Sequence[float])->float:
    if not left or len(left)!=len(right):
        return 0.0
    return float(sum(a*b for a,b in zip(left,right)))


class HashEmbeddingEncoder:
    """旧本地哈希后端，仅作为显式配置的轻量后备。"""

    model=HASH_MODEL

    def __init__(self,*,dimension:int=384)->None:
        self.dimension=int(dimension)

    def encode(self,texts:Sequence[str])->List[array]:
        return[
            embed_text(text,dimension=self.dimension)
            for text in texts
        ]


class NeuralEmbeddingEncoder:
    """多语 SentenceTransformer ONNX 编码器。

    模型使用 XLM-R SentencePiece 分词、last_hidden_state 均值池化和
    L2 归一化。ONNX 模型输出 384 维语义向量。
    """

    model=NEURAL_MODEL

    def __init__(
        self,model_dir:Path,*,batch_size:int=16,max_length:int=128
    )->None:
        try:
            import numpy as np
            import onnxruntime as ort
            import sentencepiece as spm
        except ImportError as exc:
            raise RuntimeError(
                "神经向量需要 numpy、onnxruntime 和 sentencepiece"
            ) from exc

        self.np=np
        self.model_dir=Path(model_dir)
        self.model_path=self.model_dir/"model_quint8_avx2.onnx"
        self.tokenizer_path=self.model_dir/"sentencepiece.bpe.model"
        self.tokenizer_json=self.model_dir/"tokenizer.json"
        if not self.model_path.is_file():
            raise FileNotFoundError("向量模型不存在：%s"%self.model_path)
        if not self.tokenizer_path.is_file():
            raise FileNotFoundError("向量分词模型不存在：%s"%self.tokenizer_path)
        if not self.tokenizer_json.is_file():
            raise FileNotFoundError("向量分词配置不存在：%s"%self.tokenizer_json)

        # sentencepiece 的 Windows C++ 文件接口不能可靠处理中文路径，
        # 因此只把 5MB 分词模型复制到 ASCII 运行目录。
        token_cache=(
            Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
            /"lit-agent"/"models"/NEURAL_MODEL/"sentencepiece.bpe.model"
        )
        token_cache.parent.mkdir(parents=True,exist_ok=True)
        if(
            not token_cache.is_file()
            or token_cache.stat().st_size!=self.tokenizer_path.stat().st_size
        ):
            shutil.copy2(self.tokenizer_path,token_cache)
        self.tokenizer=spm.SentencePieceProcessor(
            model_file=str(token_cache)
        )
        tokenizer_data=json.loads(
            self.tokenizer_json.read_text(encoding="utf-8")
        )
        self._token_id_map={
            item["content"]:int(item["id"])
            for item in tokenizer_data.get("added_tokens",[])
        }
        vocab=tokenizer_data["model"]["vocab"]
        normal_raw=next(
            index for index in range(self.tokenizer.get_piece_size())
            if index not in(
                self.tokenizer.unk_id(),
                self.tokenizer.bos_id(),
                self.tokenizer.eos_id(),
            )
        )
        normal_piece=self.tokenizer.id_to_piece(normal_raw)
        normal_serialized=next(
            index for index,(piece,_score) in enumerate(vocab)
            if piece==normal_piece
        )
        self._normal_id_shift=normal_serialized-normal_raw
        self.session=ort.InferenceSession(
            str(self.model_path),providers=["CPUExecutionProvider"]
        )
        self.input_names={item.name for item in self.session.get_inputs()}
        self.batch_size=max(1,int(batch_size))
        self.max_length=max(16,min(128,int(max_length)))
        output_shape=self.session.get_outputs()[0].shape
        self.dimension=int(
            output_shape[-1]
            if isinstance(output_shape[-1],int) else 384
        )

    def _encode_batch(self,texts:Sequence[str])->"Any":
        sequences=[]
        for text in texts:
            raw_ids=self.tokenizer.encode(text or "",out_type=int)
            token_ids=[
                self._token_id_map["<unk>"]
                if token_id==self.tokenizer.unk_id()
                else self._token_id_map["<s>"]
                if token_id==self.tokenizer.bos_id()
                else self._token_id_map["</s>"]
                if token_id==self.tokenizer.eos_id()
                else token_id+self._normal_id_shift
                for token_id in raw_ids
            ]
            token_ids=[
                self._token_id_map["<s>"],
                *token_ids[:self.max_length-2],
                self._token_id_map["</s>"],
            ]
            sequences.append(token_ids)
        length=max((len(item) for item in sequences),default=2)
        input_ids=self.np.zeros(
            (len(sequences),length),dtype=self.np.int64
        )
        mask=self.np.zeros_like(input_ids)
        for index,token_ids in enumerate(sequences):
            input_ids[index,:len(token_ids)]=token_ids
            mask[index,:len(token_ids)]=1
        feeds={
            "input_ids":input_ids,
            "attention_mask":mask,
        }
        if "token_type_ids" in self.input_names:
            feeds["token_type_ids"]=self.np.zeros_like(input_ids)
        hidden=self.session.run(
            ["last_hidden_state"],feeds
        )[0]
        vector=(
            hidden*mask[:,:,None]
        ).sum(axis=1)/self.np.clip(mask.sum(axis=1,keepdims=True),1,None)
        norm=self.np.linalg.norm(vector,axis=1,keepdims=True)
        return vector/self.np.clip(norm,1e-12,None)

    def encode(self,texts:Sequence[str])->List[array]:
        result:List[array]=[]
        for start in range(0,len(texts),self.batch_size):
            batch=texts[start:start+self.batch_size]
            matrix=self._encode_batch(batch)
            result.extend(
                array("f",row.astype("float32",copy=False).tolist())
                for row in matrix
            )
        return result


@lru_cache(maxsize=4)
def _neural_encoder(
    model_dir:str,*,batch_size:int=16
)->NeuralEmbeddingEncoder:
    return NeuralEmbeddingEncoder(
        Path(model_dir),batch_size=batch_size
    )


def get_embedding_encoder(
    *,backend:str="neural",model_dir:Path,batch_size:int=16,
    dimension:int=384,
)->HashEmbeddingEncoder|NeuralEmbeddingEncoder:
    if backend=="hash":
        return HashEmbeddingEncoder(dimension=dimension)
    return _neural_encoder(str(Path(model_dir).resolve()),batch_size=batch_size)


@dataclass(frozen=True)
class VectorHit:
    chunk_id:str
    doc_id:str
    score:float
    text:str=""


class VectorIndex:
    """从 SQLite 持久化向量中执行余弦检索。"""

    def __init__(
        self,store:object,*,encoder:object,dimension:int=384,
        model:str=VECTOR_MODEL,
    )->None:
        self.store=store
        self.encoder=encoder
        self.dimension=max(32,int(getattr(encoder,"dimension",dimension)))
        self.model=model

    def search(
        self,query:str,*,top_k:int=10,min_score:float=0.12
    )->List[VectorHit]:
        query_vector=self.encoder.encode([query])[0]
        rows=self.store.connect().execute(
            "SELECT v.chunk_id,v.vector,c.doc_id "
            "FROM chunk_vectors v JOIN chunks c ON c.id=v.chunk_id "
            "WHERE v.model=? AND v.dimension=?",
            (self.model,self.dimension),
        ).fetchall()
        if not rows:
            return[]
        scored:List[tuple[str,str,float]]=[]
        for row in rows:
            try:
                similarity=cosine(
                    query_vector,decode_vector(row["vector"])
                )
            except(ValueError,TypeError):
                continue
            if similarity>=min_score:
                scored.append((row["chunk_id"],row["doc_id"],similarity))
        scored.sort(key=lambda item:(-item[2],item[0]))
        scored=scored[:max(1,int(top_k))]
        texts=self.store.get_chunk_texts([item[0] for item in scored])
        return[
            VectorHit(
                chunk_id=chunk_id,
                doc_id=doc_id,
                score=round(score,6),
                text=texts.get(chunk_id,""),
            )
            for chunk_id,doc_id,score in scored
        ]

    @staticmethod
    def aggregate_by_document(
        hits:Sequence[VectorHit],*,max_parts_per_doc:int=3
    )->List[Dict[str,object]]:
        grouped:Dict[str,Dict[str,object]]={}
        for hit in hits:
            item=grouped.setdefault(
                hit.doc_id,
                {"doc_id":hit.doc_id,"score":0.0,"parts":[]},
            )
            item["score"]=max(float(item["score"]),hit.score)
            parts=item["parts"]
            if isinstance(parts,list) and len(parts)<max_parts_per_doc:
                parts.append(hit)
        return sorted(
            grouped.values(),key=lambda item:(-float(item["score"]),str(item["doc_id"]))
        )


__all__=[
    "HASH_MODEL",
    "NEURAL_MODEL",
    "VECTOR_MODEL",
    "HashEmbeddingEncoder",
    "NeuralEmbeddingEncoder",
    "VectorHit",
    "VectorIndex",
    "cosine",
    "decode_vector",
    "embed_text",
    "encode_vector",
    "get_embedding_encoder",
]
