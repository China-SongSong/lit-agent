# 测试资产

本目录集中存放 lit-agent 的测试脚本、过程数据与固定测评集，供复现测试结果使用。
所有脚本以只读方式调用 `作品相关代码/scr` 下的模块，不对生产库写入。

## 目录结构

    scripts/regression   功能回归与缺陷复测（28 个）
    scripts/modules      模块专项覆盖（15 个）
    scripts/params       参数扫描与性能剖析（22 个）
    scripts/eval         评测集构建、校准与报告工具（13 个）
    scripts/diagnostics  一次性诊断与探针（27 个，多为历史排查用）
    data                 测试过程产物（数据库副本、JSON 结果、引文片段）
    datasets             固定测评集镜像（8 个）

## 环境

Windows 10/11，Python 3.14（3.9 以上即可），依赖 requests、jieba、PyMuPDF、streamlit。
脚本内部已写死 `作品相关代码/scr` 的绝对路径，因此在任意工作目录下执行均可。

## 常用命令

仓库自带单元测试（不依赖本目录）：

    cd 作品相关代码/scr
    python -B -m unittest discover -s tests

功能回归：

    python 测试资产/scripts/regression/fresh_reg.py
    python 测试资产/scripts/regression/fresh_ui.py

模块专项（Agent 主循环、RRF 融合、检索降级、输出解析、去重、引文、系统验收）：

    python 测试资产/scripts/modules/t_agent.py
    python 测试资产/scripts/modules/t_rrf.py
    python 测试资产/scripts/modules/t_json_dedupe.py
    python 测试资产/scripts/modules/t_cite_calib.py
    python 测试资产/scripts/modules/t_baseline_offline.py
    python 测试资产/scripts/modules/t_e2e.py
    python 测试资产/scripts/modules/t_summary_cache.py

参数扫描：

    python 测试资产/scripts/params/p_env.py
    python 测试资产/scripts/params/p_sem2.py
    python 测试资产/scripts/params/p_bm25.py

评测集评测与阈值校准：

    cd 作品相关代码/scr
    python -B -m litagent.evaluation.calibration `
      --corpus tests/fixtures/calibration_corpus_v2.jsonl `
      --queries tests/fixtures/calibration_queries_v3.jsonl --format markdown

用例级评测（输出逐条通过情况，需先运行 eval_v3.py）：

    python 测试资产/scripts/eval/eval_v3.py

## 数据约定

- `data/` 内的数据库均为生产库副本，脚本用完即删；如残留可直接删除，不影响项目。
- 写操作用例（删除、重建索引、文献库删除）一律针对副本执行，测试前后各做一次计数与完整性比对。
- `datasets/` 是 `作品相关代码/scr/tests/fixtures/` 的镜像，**权威版本在 fixtures 目录**。
  修改测评集请改 fixtures，再复制回来，避免两处不一致。同步命令：

      copy 作品相关代码\scr\tests\fixtures\calibration_queries_v3.jsonl 测试资产\datasets\

## 说明

`scripts/diagnostics/` 中的脚本是历次排查时随手写的探针，多数只对特定版本有效，
保留是为了追溯当时的判断依据，不建议作为常规测试入口。
