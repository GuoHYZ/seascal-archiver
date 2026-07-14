# Seascal's Archiver — 办公文档批量归档与语义检索

将 PPTX / DOCX / XLSX 办公文档批量转换为纯文本 TXT（表格保留为 Markdown），构建 FAISS + BM25 混合索引，支持语义 + 关键词检索。提供 CLI / 交互菜单 / HTTP 后端三种使用方式。

## 快速开始

### 1. 安装

```bash
pip install -r requirements.txt
```

### 2. 文档转换

```bash
python archive.py D:\公司文档              # 全量转换
python archive.py D:\公司文档 -i           # 增量更新（仅处理变更文件）
```

输出到 `D:\公司文档_TXT_archive\`，镜像目录结构，自动生成 `_索引.txt`。

### 3. 构建索引

```bash
python search/build_rag.py D:\公司文档_TXT_archive    # 增量构建 FAISS + BM25
python search/build_rag.py D:\公司文档_TXT_archive --force  # 强制全量重建
```

### 4. 检索

```bash
# 交互菜单（推荐）
python main.py

# 命令行检索
python search/search_rag.py "华润燃气合同编号" --db D:\公司文档_TXT_archive

# 启动 HTTP 后端
python search/search_backend.py --db D:\公司文档_TXT_archive
```

## 功能特性

- **多格式支持**：PPTX、DOCX、XLSX，旧格式 (.doc/.ppt/.xls) 通过 MS Office 自动转换
- **增量更新**：SHA256 内容指纹，仅处理变更文件，跨设备复制后仍准确
- **智能增量索引**：Embedding 向量缓存复用，仅对新增/变更 chunk 编码，增量构建速度提升数十倍
- **超大表格优化**：读取行数可配置（`XLSX_MAX_ROWS`），默认无限制
- **混合检索**：BM25（关键词精确命中）+ 向量（语义关联）+ RRF 排名融合
- **多硬件加速**：CUDA / MPS / CPU 自动检测，CPU 多线程优化
- **容错设计**：单文件失败不影响整体，单页异常不丢整个文档

## 命令行参考

| 命令 | 说明 |
|------|------|
| `python main.py` | 交互式菜单（推荐） |
| `python archive.py <目录> [-i] [--clean]` | 文档转换 |
| `python search/build_rag.py [归档目录] [--force]` | 构建索引 |
| `python search/search_rag.py "查询" --db <归档目录> [--llm]` | CLI 检索 |
| `python search/search_backend.py --db <归档目录>` | HTTP 后端 |

## 项目结构

```
Seascal's Archiver/
├── main.py                     ← 统一交互入口
├── archive.py                  ← 文档转换主入口
├── rag_config.py               ← RAG 全局配置
├── rag_utils.py                ← 共享工具（Embedding / BM25 / RRF）
├── converters/                 ← 文档转换模块
│   ├── pptx2txt.py / docx2txt.py / xlsx2txt.py
│   └── legacy2new.py           ← 旧格式转换（需 MS Office）
├── search/                     ← RAG 检索模块
│   ├── build_rag.py            ← 索引构建
│   ├── search_rag.py           ← CLI 检索 + LLM 回答
│   └── search_backend.py       ← HTTP 检索后端
└── docs/                       ← 提示词文件
```

## 关键配置

编辑 `rag_config.py`：

```python
CHUNK_MAX_CHARS = 800     # 文本块最大字符数
SEARCH_TOP_K = 5           # 返回结果数
XLSX_MAX_ROWS = 0          # Excel 最大读取行数（0=不限制）

HYBRID_ENABLED = True      # 混合检索开关
RRF_K = 60                 # 排名融合平滑常数

LLM_MODEL_NAME = "deepseek-chat"  # LLM 回答模式
```

## 检索模式

| 模式 | 说明 |
|------|------|
| **混合检索**（默认） | BM25 命中精确词/编号 + 向量捕获语义 → RRF 融合 |
| **纯向量回退** | BM25 索引未构建时自动切换 |

每路召回 `top_k × 3` 个候选，RRF 融合后截取前 `top_k` 个。结果含 `file_path` 字段（文档绝对路径），方便 Agent 读取完整文件。

## 已知限制

- 图片、图表、SmartArt 中内容无法提取
- 不提取批注、修订痕迹、页眉页脚
- 旧格式需本机安装 Microsoft Office
- 语义检索对大范围列表型查询可能不完整，建议结合 `_索引.txt`

## 扩展新格式

1. 在 `converters/` 下编写 `xxx2txt.py`，实现 `convert(source_path, dest_path) -> int`
2. 在 `archive.py` 的 `SUPPORTED_FORMATS` 添加一行