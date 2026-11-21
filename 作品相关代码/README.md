# lit-agent 文献引文溯源系统

输入一段话，找出它出自本地语料库里的哪篇文献的哪一段，并给出 GB/T 7714-2015 格式的引用。
另外还有文献检索、结构化总结和对话三项能力。

除抓取文献和调用大模型之外，其余步骤都能在本机离线完成。

## 怎么跑起来

双击 `作品相关代码` 文件夹里的 `一键运行.bat`，剩下的交给它：检查 Python、装依赖、建索引、开网页。
浏览器大概 8 秒后自动打开 <http://127.0.0.1:8501>，没自动打开就手动输一下地址。
关掉命令行窗口，服务就停了。

只想确认环境有没有问题、不想开网页，在命令行里加上 check：

```
一键运行.bat check
```

更细的说明在 `一键运行说明.txt` 里。

## 开发与验收

这个项目把 AI 当作开发辅助。需求拆分、数据范围、阈值取舍和最终验收由开发者决定；AI 主要用于起草代码、检查边界情况和整理说明文字。每次改动都要通过模块导入、环境自检、语料统计和实际溯源样例，未经验证的建议不直接进入交付版本。

## 离线评测与校准

`scr/tests` 保存版本化评测语料、负样本和契约回归。生产库只读基线记录在
`production_baseline_v3.json`（v1、v2 保留为旧哈希口径历史记录）；Phase 0 的真实语料副本通过 SQLite 快照构建，
不会直接修改生产库。

在 `scr` 目录运行：

```powershell
python -B -m unittest discover -s tests -v
python -B -m litagent.evaluation.calibration --corpus tests/fixtures/calibration_corpus_v2.jsonl --queries tests/fixtures/calibration_queries_v2.jsonl
```

BM25 只作为诊断旁证记录，不再向置信度增加固定分数。`found=true` 必须由
本地精确指纹证据产生；没有显式联网许可时，溯源链路不得发起任何外部请求。

## 运行环境

Windows 10 / 11，Python 3.9 及以上（开发时用的是 3.14.7）。
需要四个包：requests、jieba、PyMuPDF、streamlit，都写在 `requirements.txt` 里。

```
pip install -r requirements.txt
```

普通笔记本就能跑，不用显卡，也不用另外装数据库。

## 要不要填密钥

引文溯源、文献检索、清洗、建索引这四项都不需要密钥，也不联网，装完就能演示。

只有网页里的“对话”页签和“总结”功能要调用大模型，需要在 `.env` 里填一行：

```
LIT_LLM_API_KEY=你的密钥
```

把 `.env.example` 复制一份改名成 `.env` 再填就行。密钥在
<https://platform.deepseek.com/> 申请。因为密钥不方便公开，交付时 `.env`
里可能没有有效密钥，这不影响上面四项的演示。

## 检索是怎么做的

召回层在本地工作，分为三种用途：

- 精确 n-gram 指纹：唯一能够确认原文出处的证据层。
- BM25 关键词：找术语命中，只记录诊断旁证，不改变置信度。
- 本地神经向量：用多语 MiniLM 覆盖同义改写、概念概述和跨语言表达，
  只在精确路径未命中或弱命中时按需启动。

向量结果不能单独设置 `found`。只有向量命中时显示为 `local_semantic`，
置信度保持为零，并明确标注“非原文证据”；指纹弱命中且向量指向同一文献时，
只记录软旁证，不把 BM25 与向量分数简单相加。当前默认使用本地量化 ONNX
模型 `paraphrase-multilingual-MiniLM-L12-v2-int8`，维度 384。模型位于
`data/models/paraphrase-multilingual-MiniLM-L12-v2`，运行时不访问外网。

向量参数：

```text
LIT_VECTOR_ENABLED=true
LIT_VECTOR_BACKEND=neural
LIT_VECTOR_DIM=384
LIT_VECTOR_MODEL_DIR=data/models/paraphrase-multilingual-MiniLM-L12-v2
LIT_VECTOR_BATCH_SIZE=16
LIT_VECTOR_TOP_K=10
LIT_VECTOR_MIN_SCORE=0.12
LIT_VECTOR_TRIGGER_RATIO=0.85
```

## 语料库里有什么

`data` 目录里带了一份可以直接演示的语料：

- 文献 49 篇（全文 8、摘要 31、仅标题 10）
- 正文片段 254 个，约 3.1 万词
- 精确指纹 47834 条
- 结构化总结 8 篇

第一次运行不会重新下载这些内容。建索引是增量的，已经建好的文献会自动跳过，
所以第二次运行通常只要几秒。

想从零开始的话，把整个 `data` 目录删掉，再双击 `一键运行.bat`，会重建一个空库，
之后用 `python cli.py fetch` 重新抓文献。

## 溯源结果怎么看

1. 只能查出本地库里的文献。库里没有的，本地检索在原理上就不可能命中，系统会
   直说没有找到，不会编一个出处出来。
2. 设计上宁可漏报也不误报。只有置信度达到阈值、等级是“精确”或“高度相似”时，
   才判定为确认找到。
3. 勾“本地找不到时联网”之后返回的那些候选不算出处。它们是“可能相关”的线索，
   界面上会标明本地库未收录，需要自己核实。实际用下来经常是几条相关、几条不太相关。
4. 每条结论都带诊断信息：词元数、指纹窗口、候选数量、有没有 BM25 旁证、
   有没有跨片段拼接、有没有联网、花了多少时间，判断依据都在里面。
5. 引用之前还是要人工核对一遍原文，这是学术规范的要求。

## 联网兜底

本地没找到、并且勾了联网时，英文查询会查 OpenAlex、Crossref、arXiv 和
Semantic Scholar；中文查询查 OpenAlex、Crossref 以及中文浏览器来源。
结果跨源去重后最多显示 8 条。

OpenCLI 是内置的二级联网补充通道：中文查询同时路由到 CNKI、万方和百度学术，
其中 CNKI/万方使用题录关键词，百度学术使用精确短语；英文查询使用 Google Scholar。
结果会按查询语种过滤，外文候选不会挤占中文主列表。它不会在离线检索中启动。
浏览器桥不可用、验证码、
登录过期或超时只会记录为补充通道错误，不会阻断四个结构化 API。OpenCLI 候选
始终排在 API 候选之后，并且只作为外部候选，不参与本地 `found` 判定。

有几点要注意：

- Semantic Scholar 不填密钥基本查不了，会返回 429 限流。需要在 `.env` 里填
  `LIT_S2_API_KEY`，密钥去它官网申请。没填的话这一路会报一条错，另外三路照常返回。
- arXiv 官方限制大约 3 秒一次，网络状况不好时可能在等待窗口内没返回，会被跳过。
- 任何一个库失败都不影响其它库，也不会中断溯源。哪个库没回来，诊断信息里会写清楚。
- 建议在 `.env` 里填一个 `LIT_CONTACT_EMAIL`。OpenAlex 和 Crossref 对匿名请求给的
  额度比较低，填了会稳一些。
- Agent 的 `fetch_online` 默认只预览候选，不写入本地库；确认需要入库后，必须显式传
  `preview=false`。这样可以避免一次对话改变语料基线。
- OpenCLI 需要 Node.js、OpenCLI Browser Bridge 扩展和浏览器。缺少扩展时系统仍可
  正常使用 API 与本地检索；建议用 `LIT_OPENCLI_PROFILE` 指定专用浏览器配置。
  首次使用中文站点时运行 `启动OpenCLI浏览器.bat`，在专用窗口中手动登录或完成
  安全验证，并保持窗口开启。交互式 CNKI 查询默认最多等待 35 秒。项目使用
  `kns.cnki.net/kns8s` 国内入口；安装或重新安装本地适配器时运行
  `安装OpenCLI知网修复.bat`。遇到滑块验证会明确提示 `CNKI_AUTH_REQUIRED`。

溯源页可选填 DOI 或包含 DOI 的来源链接；联网候选卡片提供“打开原文 / 来源页”
按钮。没有可直接访问 URL 时，会用 DOI 生成 `https://doi.org/<DOI>` 跳转链接。

文献会自动生成学科标签，例如“新闻传播学类”“社会学类”“历史学类”，一篇
文献可以同时拥有多个交叉学科标签。标签保存在独立的 `document_tags` 表中，
不改变书目语料哈希；文献库支持按标签筛选和手动重建全部标签。

网页新增“查询历史”页签，持久保存 Agent 对话和溯源查询，可查看当时的候选、
打开候选链接、把历史查询载入溯源页，或删除单条/清空历史记录。

- 对已有总结执行 `--redo` 会绕过旧缓存重新生成，适合修复历史上字段不完整的总结；
  新旧结果按字段覆盖度合并，字段更少的重生成结果不会清空原有字段。

## 常见问题

- 提示找不到 Python：装 Python 的时候没勾 “Add Python to PATH”，重装时勾上就好。
- 依赖装得很慢：换成国内镜像再装一次。
  `pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`
- 浏览器没自动打开：手动访问 <http://127.0.0.1:8501>。
- 端口被占用：`python cli.py ui --port 8502`，然后访问 8502 端口。
- 页面里显示没有文献：`data` 目录被清空了，用
  `python cli.py fetch -q "图神经网络 推荐系统" --limit 20` 抓一些回来。
- 溯源说没找到：这段话的文献不在库里，属于正常现象，可以勾联网看看有没有线索。
- 对话报错：没填 `LIT_LLM_API_KEY`，不影响溯源和检索。
- 想确认环境：`一键运行.bat check`，或者 `python cli.py doctor`。

## 目录结构

```
lit_agent/                        作品总目录
├─ lit-agent 作品设计报告.docx     设计报告
├─ 演示视频.mp4
├─ 参赛报名表.xlsx
├─ .env                           配置，放密钥和联系邮箱
├─ .env.example                   配置模板
└─ 作品相关代码/
   ├─ README.md                   本文件
   ├─ 一键运行.bat                一键运行，推荐入口
   ├─ 一键运行说明.txt            一键运行的中文分步说明
   ├─ 启动网页.bat                只启动网页的旧入口
   ├─ requirements.txt            依赖清单
   └─ scr/                        程序代码
      ├─ cli.py                   命令行入口
      ├─ app.py                   网页界面
      ├─ litagent/                主体包
      │  ├─ fetch/                阶段 1：在线抓取与本地 PDF 导入
      │  ├─ clean/                阶段 2：正文抽取、分块、去重
      │  ├─ index/                阶段 3：存储、BM25、n-gram 指纹、RRF 融合
      │  ├─ retrieve/             阶段 4：混合检索与引文溯源
      │  ├─ summarize/            结构化总结
      │  ├─ agent/                对话式 Agent
      │  ├─ cite/                 引用格式
      │  └─ llm/                  大模型客户端
      └─ data/                    预置演示语料
```

两个启动脚本会自己去找 `cli.py` 所在的目录，代码放在 `scr` 子目录里可以，
直接和 bat 放在同一层也可以，两种摆法都能跑。`.env` 放哪一层也能自动找到。

## 命令行

```
python cli.py doctor                  环境自检，加 --probe 会真的调一次大模型
python cli.py init                    建库，目录 + SQLite 表结构
python cli.py fetch -q "主题" --limit 20    联网抓文献
python cli.py fetch --local --path "D:\我的PDF"    导入本地 PDF
python cli.py download --limit 10     下载开放获取的全文 PDF
python cli.py clean                   清洗，抽正文、分块，可选去重
python cli.py index                   建词法、指纹和向量索引，默认增量
python cli.py index --rebuild         全量重建索引
python cli.py trace "一段要溯源的话"   命令行溯源，--no-online 关掉联网
python cli.py chat -i                 对话，需要大模型密钥
python cli.py summarize               批量结构化总结，需要大模型密钥
python cli.py stats                   语料统计
python cli.py ui                      启动网页界面
```

## 已知问题

- 语料少的时候漏报会明显变多。
- 神经向量模型较小，长文档概述、领域外概念和复杂跨语言比喻仍可能漏召回；
  向量候选始终需要人工核对，不得直接作为原文出处。
- 扫描版 PDF 抽不出文字时只剩题录。PyMuPDF 抽不出来会再试一次 pdfminer.six，
  这个包可选安装。
- Semantic Scholar 和 arXiv 受对方限流影响，联网时偶尔个别库没有结果。
- 系统只给判断依据和置信度，不作学术结论，引用前请自行核对原文。
