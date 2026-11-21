# 固定测评集镜像

本目录文件来自 `作品相关代码/scr/tests/fixtures/`，仅供查阅与离线复算。
权威版本在 fixtures 目录，测试代码引用的是那里，不是这里。

## 文件

- calibration_corpus_v2.jsonl：评测语料 8 篇（6 全文、1 摘要、1 题录）
- calibration_queries_v2.jsonl：第二版查询集 14 条（calibration 9 / test 5）
- calibration_queries_v3.jsonl：第三版查询集 51 条（正 37 / 负 14），v2 全量保留
- production_queries_v1.jsonl：生产库校准查询 9 条
- production_baseline_v1/v2/v3.json：生产语料只读基线，逐版收紧哈希口径
- evaluation_manifest_v2.json：评测集清单

## 版本关系

v1 基线按整库摘要，任何总结重生成都会令其失效；
v2 改为按表摘要，但仍把 `documents.summarized_at` 计入；
v3 只摘要书目语义字段，仅覆盖 `documents` 与 `documents_fulltext`，并排除抽取时间、清洗时间、
总结时间、PDF 路径及全部派生索引。判断语料是否变化使用 v3。

## 同步

    copy 作品相关代码\scr\tests\fixtures\*.json* 测试资产\datasets\
