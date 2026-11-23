# lit-agent 文献引文溯源系统

输入一段话，找出它出自本地语料库里的哪篇文献的哪一段，并给出 GB/T 7714-2015
格式的引用。系统还提供文献检索、神经语义召回、结构化总结、对话、学科标签和
查询历史。

除抓取文献、联网补充候选和调用大模型之外，其余步骤都能在本机离线完成。

## 适合什么场景

- 找到一段话在本地论文中的具体章节、页码和原文片段。
- 检查改写、转述或跨语言概述可能对应哪些本地文献。
- 从开放学术接口或中文浏览器来源扩大候选文献范围。
- 为一个本地 PDF 语料建立可离线使用的检索和引用系统。
- 让其他程序通过带参数的 URL 直接向 Agent 提交问题。

## 五分钟上手

### 1. 获取项目和模型

先安装 Git、Git LFS 和 Python 3.12 或更高版本，然后执行：

```powershell
git clone https://github.com/China-SongSong/lit-agent.git
cd lit-agent
git lfs install
git lfs pull
```

`git lfs pull` 用于下载本地神经向量模型。如果
`作品相关代码/scr/data/models` 下的 ONNX 文件只有几十字节，说明仍是 LFS
指针，需要重新执行该命令。

### 2. 配置（可选）

离线检索和原文溯源不需要密钥。只有 Agent 对话与结构化总结需要大模型：

```powershell
Copy-Item .env.example .env
notepad .env
```

在 `.env` 中填写：

```env
LIT_LLM_API_KEY=你的DeepSeek密钥
```

如果允许其他系统通过 URL 自动执行问题，再设置一个随机令牌：

```env
LIT_URL_AUTORUN_TOKEN=换成一个足够长的随机字符串
```

### 3. 一键启动

双击：

```text
作品相关代码/一键运行.bat
```

脚本会完成以下步骤：

1. 检查 Python 与锁定版本依赖。
2. 创建数据库和数据目录。
3. 增量建立 BM25、精确指纹和神经向量索引。
4. 启动带 OpenCLI Browser Bridge 的专用浏览器。
5. 打开网页 <http://127.0.0.1:8501>。

只检查环境、不启动网页：

```powershell
作品相关代码\一键运行.bat check
```

### 4. 第一次打开网页

网页包含四个页签：

- **溯源**：粘贴一段文字，点击“开始溯源”。勾选“本地找不到时联网”后才会访问
  外部来源。
- **文献库**：查看已入库文献、学科标签、内容级别、摘要或全文。
- **与 Agent 对话**：用自然语言调用检索、溯源、详情和总结工具，需要 LLM Key。
- **查询历史**：回看此前的溯源和 Agent 问题，重新载入或打开候选链接。

首次使用中文联网来源时，Edge 可能要求启用 OpenCLI Browser Bridge 扩展。
按一键脚本自动打开的扩展管理页提示操作一次，然后保持专用浏览器窗口开启。

## 页面操作示例

### 检查一段原文

1. 打开“溯源”页。
2. 将一段论文原文粘贴到“待溯源的文本”。
3. 点击“开始溯源”。
4. 查看置信度、匹配等级、所在章节、页码、原文上下文和 GB/T 7714 引用。

如果结果显示“语义相关文献（非原文证据）”，说明本地没有足够的逐字证据，
不能把该结果直接当作原文出处。

### 查找一个主题

可以直接问 Agent，例如：

```text
帮我找几篇讨论推荐系统冷启动的论文，并说明它们的方法差异。
```

也可以使用命令行：

```powershell
Set-Location 作品相关代码\scr
python cli.py search "推荐系统 冷启动" --documents
```

### 导入自己的 PDF

```powershell
Set-Location 作品相关代码\scr
python cli.py fetch --local --path "D:\我的论文"
python cli.py clean
python cli.py index
```

重新打开或刷新网页后，即可检索和溯源新语料。

## 外部系统通过 URL 提问

普通链接只预填问题，用户确认后才执行：

```text
http://127.0.0.1:8501/?question=帮我查找冷启动论文
```

加入 `autorun=1` 后，页面载入时直接把问题送入 Agent：

```text
http://127.0.0.1:8501/?question=帮我查找冷启动论文&autorun=1
```

如果配置了 `LIT_URL_AUTORUN_TOKEN`，还必须携带令牌：

```text
http://127.0.0.1:8501/?question=问题&autorun=1&token=配置的令牌
```

溯源链接示例：

```text
http://127.0.0.1:8501/?mode=trace&q=要溯源的文字&online=1
```

自动运行会立即产生模型费用。不要把带令牌的链接发布到公开页面；服务器也不应
直接暴露到公网。

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
- 可通过 URL 参数预填 Agent 问题或溯源文本，并支持显式授权的自动运行。
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

## 常见问题

以下命令行示例均以 `作品相关代码/scr` 为当前目录。

- **8501 端口被占用**：在 `作品相关代码/scr` 执行
  `python cli.py ui --port 8502`，再访问 `http://127.0.0.1:8502`。
- **模型文件只有几十字节**：执行 `git lfs install` 和 `git lfs pull`。
- **页面提示没有文献**：`data` 可能为空，执行
  `python cli.py fetch -q "主题" --limit 20` 后重新清洗和建索引。
- **溯源没有找到**：只检查已入库全文；库外文献需要勾选联网或先导入语料。
- **Agent 提示未配置 Key**：填写 `LIT_LLM_API_KEY`，本地检索与溯源不受影响。
- **中文来源要求登录或验证**：在 OpenCLI 专用浏览器中完成一次人工验证；
  系统不会绕过滑块、登录或机构权限。
- **URL 自动运行被降级为草稿**：检查 `.env` 中的
  `LIT_URL_AUTORUN_TOKEN` 和链接里的 `token` 是否一致。
- **需要排查运行问题**：查看 `作品相关代码/scr/data/logs/litagent.log`，
  或运行 `python cli.py doctor`。

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
