# lit-agent 离线评测

本目录保存 Phase 0 的契约回归、评测语料和阈值校准测试。生产索引只读，
评测库在临时目录中从 JSONL 重建。

## 固定基线

`fixtures/production_baseline_v3.json` 按语义语料逻辑哈希口径记录当前生产库的
版本、总体与分表 SHA-256、查询集哈希和语料统计。
`production_baseline_v1.json` 和 `v2` 保留为旧哈希口径的历史记录。修改生产语料
前应先复制数据库并生成新的版本清单，不能直接覆盖已有基线。

## 评测集

`fixtures/calibration_corpus_v2.jsonl` 和
`fixtures/calibration_queries_v3.jsonl` 是隔离的合成评测集，覆盖：

- 中英文全文、摘要和仅标题；
- 逐字原文、标点变化、短句和部分命中；
- 同义改写、概念概述和多文献综合概述；
- 同主题不同结论、库外相似文本和无关文本。

`split=calibration` 用于选择阈值，`split=test` 用于验收。不得用 test
样本反复调参。

## 运行测试

在 `scr` 目录执行：

```powershell
python -B -m unittest discover -s tests -v
```

在仓库根目录安装开发依赖并运行静态检查：

```powershell
python -m pip install -r requirements-dev.txt
ruff check 作品相关代码/scr
```

GitHub Actions 的 `CI` 工作流会检出 Git LFS 模型、安装锁定依赖和项目内
OpenCLI、执行 Ruff、编译 Python，并运行上述完整测试。

## 阈值校准

```powershell
python -B -m litagent.evaluation.calibration `
  --corpus tests/fixtures/calibration_corpus_v2.jsonl `
  --queries tests/fixtures/calibration_queries_v3.jsonl `
  --format markdown
```

默认采用精度优先策略：只有精确率达到 0.98 才比较召回；召回相同时选择
最接近现有基线 0.72 的阈值。校准结果只记为观察候选，不自动修改运行时
默认阈值。BM25 只作为诊断字段，不增加置信度，也不自动触发 0.68。

当前评测集的校准结论写入 `data/evaluation/`。它只是 Phase 0 的合成基线，
不能替代真实论文和真实用户查询上的最终验收。

## 向量召回

`data/models/paraphrase-multilingual-MiniLM-L12-v2` 保存量化 ONNX 多语
MiniLM 模型、SentencePiece 分词文件和模型清单。模型由 Git LFS 管理，测试前
应确认文件不是 LFS 指针，并执行：

```powershell
python -B cli.py index
```

向量只在精确指纹未命中或覆盖率低于触发线时启动；测试覆盖模型编码归一化、
同义与跨语言语义区分、全量片段向量写入，以及“只有向量命中不能设置 found”。

## 上线验收与黑盒回归

`test_acceptance_fixes.py` 覆盖本轮上线验收和黑盒反馈：

- 全文阅读区必须使用深色正文、正常行距、安全转义和滚动容器；
- 中文查询只把同语种语义候选放进主结果，跨语种候选必须降级；
- CNKI 来源链接只提取 `filename`，`invoice`、`nonce` 等会话参数不得进入
  查询历史或诊断信息；
- 超过 20,000 字的输入在进入索引前直接拒绝；
- CLI 和网页初始化后必须建立 `data/logs/litagent.log` 轮转日志。
- 中文联网路由不得请求 Crossref；引号内核心概念必须进入检索式；
- OpenCLI 扩展未连接时必须在启动站点任务前快速失败，并返回可操作提示。
- URL 预填必须区分 Agent 问题与溯源文本，清理控制字符并限制长度；
  只有显式 `autorun=1` 才能自动执行，配置令牌时必须精确匹配。
- 网页导入必须支持文件夹和多文件上传，覆盖 PDF、DOCX、DOC、RTF、TXT、
  Markdown 与 HTML，并在导入后自动完成清洗、分块和三类索引。

## 真实语料副本

需要在不修改生产库的前提下补充本地全文时，可构建隔离版本：

```powershell
python -B -m litagent.evaluation.corpus_build `
  --source-db data/index.db `
  --pdf-dir data/pdfs `
  --output-dir data/evaluation/corpus_v2 `
  --version evaluation-v2
```

该命令通过 SQLite backup API 创建快照，扫描 PDF、增量清洗、跨文献去重和
建索引，并在输出目录写入 `manifest.json`。目标目录已存在时命令会停止，
避免覆盖旧版本。清单同时记录物理文件哈希和书目表逻辑哈希；判断语料是否
变化应使用逻辑哈希，因为 WAL 合并会改变物理文件字节但不会改变数据。
逻辑哈希只覆盖书目语义字段和正文，并按表分别记录；抽取/清洗/总结时间、
分块与倒排索引、总结、模型缓存、入库日志、元数据和学科标签的变化都不会
再把语料变化误判。

## 硬约束

1. `found=true` 必须由本地指纹证据产生，主命中必须来自 `phrase`，且存在
   实际匹配文本。在线、BM25 和向量候选都不能单独设置 `found`。
   只有向量命中时等级为 `local_semantic`，置信度为零。
2. 没有显式 `allow_online=True` 时，溯源路径不得调用任何外部 provider。
   OpenCLI 接入后也必须经过 `OnlineRequest.require_online()`。

契约定义位于 `litagent/contracts.py`。新增来源应实现统一候选结构，而不是
向 Agent 或网页直接泄露上游专有字段。
