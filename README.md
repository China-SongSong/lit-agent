# lit-agent 文献引文溯源系统

输入一段话，找出它出自本地语料库里的哪篇文献的哪一段，并给出 GB/T 7714-2015
格式的引用。系统还提供文献检索、神经语义召回、结构化总结、对话、学科标签和
查询历史。

除抓取文献、联网补充候选和调用大模型之外，其余步骤都能在本机离线完成。

## 快速开始

进入代码目录并双击 `一键运行.bat`：

```text
作品相关代码/一键运行.bat
```

脚本会检查 Python 依赖、初始化数据库、建立 BM25、精确指纹和神经向量索引，
启动 OpenCLI Browser Bridge，然后打开 <http://127.0.0.1:8501>。一键入口同时
负责本地网页和联网浏览器补充通道，不需要再单独启动第二个脚本。

从 GitHub 克隆后，如果神经向量模型显示为 Git LFS 指针，需要先执行：

```powershell
git lfs pull
```

## 主要能力

- 本地精确指纹溯源，只有本地证据可以确认 `found=true`。
- BM25 关键词召回与诊断，不向置信度增加固定分数。
- 本地多语神经向量召回，覆盖同义改写、概念概述和跨语言表达。
- 中文联网补充来源，包含 CNKI、万方和百度学术。
- 文献检索、结构化总结、GB/T 7714、BibTeX 和 RIS 引用导出。
- 本地溯源、联网候选和文献库详情均可查看全文、摘要或原文入口。
- 全文阅读区使用深色正文、正常段距和自动换行，长文在框内滚动。
- 自动学科标签、按标签筛选和标签重建。
- 持久查询历史，可回看候选链接并重新载入溯源。
- 命令行支持本地检索、按文献聚合、JSON 输出，以及轮转日志。

## 证据边界

本地精确指纹是唯一能够确认原文出处的证据层。BM25、向量和联网候选只能提供
召回或旁证，不能单独把结果设为找到。只有向量命中时等级为 `local_semantic`，
置信度为零，并明确标注“非原文证据”。

## 运行环境

Windows 10 / 11，Python 3.12 及以上。依赖使用精确版本锁定，保证环境可复现。
完整依赖见
[`作品相关代码/requirements.txt`](作品相关代码/requirements.txt)。

```powershell
pip install -r 作品相关代码/requirements.txt
```

## 完整文档

- [更新日志](CHANGELOG.md)
- [项目完整说明](作品相关代码/README.md)
- [一键运行说明](作品相关代码/一键运行说明.txt)
- [测试与固定基线说明](作品相关代码/scr/tests/README.md)
- [数据字段说明](作品相关代码/字段.md)

## 测试

```powershell
Set-Location 作品相关代码/scr
python -B -m unittest discover -s tests -v
```

命令行检索：

```powershell
python cli.py search "图神经网络 推荐系统" --documents
```

固定评测集位于
[`作品相关代码/scr/data/evaluation/large_benchmark_v1`](作品相关代码/scr/data/evaluation/large_benchmark_v1)。

引用前请人工核对原文。联网候选、语义候选和任何外部检索结果都不等同于已核实出处。

[![Star History Chart](https://api.star-history.com/svg?repos=China-SongSong/lit-agent&type=Date)](https://star-history.com/#China-SongSong/lit-agent&Date)

如果这个项目对你有帮助，欢迎点一下 Star。
