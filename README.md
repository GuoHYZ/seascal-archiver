# 办公文档批量归档与语义检索工具

将 PPTX / DOCX / XLSX 办公文档批量转换为纯文本 TXT（表格保留为 Markdown），构建 FAISS + BM25 混合索引，支持语义 + 关键词检索。

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

输出到 `D:\公司文档_TXT归档\`，镜像目录结构，自动生成 `_索引.txt`。

### 3. 构建索引

```bash
python build_rag.py D:\公司文档_TXT归档    # 增量构建 FAISS + BM25
python build_rag.py D:\公司文档_TXT归档 --force  # 强制全量重建
```

### 4. 检索

```bash
# 混合检索（BM25 + 向量，默认启用）
python search_rag.py "华润燃气合同编号" --db D:\公司文档_TXT归档

# 检索 + LLM 回答
python search_rag.py "2024年新建基站数量" --db D:\公司文档_TXT归档 --llm

# 启动 HTTP 检索后端（供外部应用调用）
python search_backend.py --db D:\公司文档_TXT归档
# → http://127.0.0.1:8765/search?q=你的问题
```

## 功能特性

- **多格式支持**：PPTX、DOCX、XLSX，旧格式 (.doc/.ppt/.xls) 通过 MS Office 自动转换
- **增量更新**：SHA256 内容指纹，仅处理变更文件，跨设备复制后仍准确
- **超大表格优化**：读取行数可配置（`XLSX_MAX_ROWS`），默认无限制
- **混合检索**：BM25（关键词精确命中）+ 向量（语义关联）+ RRF 排名融合
- **GPU 加速**：CUDA 自动检测，无 GPU 时回退 CPU
- **容错设计**：单文件失败不影响整体，单页异常不丢整个文档

## 命令行参考

```bash
# 文档转换
python archive.py <目录> [输出目录] [-i] [--clean]

# 索引构建
python build_rag.py [TXT归档目录] [--force]

# 检索
python search_rag.py "查询" --db <TXT归档目录> [--llm]

# HTTP 后端
python search_backend.py --db <TXT归档目录> [--host 127.0.0.1] [--port 8765]
```

## 项目结构

| 文件 | 用途 |
|------|------|
| `archive.py` | 主入口：扫描、转换、索引生成、增量管理 |
| `rag_config.py` | RAG 全局配置（模型、切分、混合检索参数） |
| `rag_utils.py` | 共享工具：Embedding 加载、BM25 索引、RRF 融合、单元格转义 |
| `converters/pptx2txt.py` | PPTX → TXT 转换 |
| `converters/docx2txt.py` | DOCX → TXT 转换（段落 + Markdown 表格） |
| `converters/xlsx2txt.py` | XLSX → TXT 转换（多 Sheet → Markdown） |
| `converters/legacy2new.py` | 旧格式转换（.doc/.ppt/.xls，需 MS Office） |
| `search/build_rag.py` | 索引构建：文本切分 → Embedding → FAISS + BM25 |
| `search/search_rag.py` | CLI 检索：混合检索 / 纯向量 / LLM 回答 |
| `search/search_backend.py` | HTTP 检索后端（REST API） |
| `docs/` | 提示词文件：`agent_prompt.txt`、`文档库检索提示词.md` |

## 关键配置

编辑 `rag_config.py`：

```python
CHUNK_MAX_CHARS = 800     # 文本块最大字符数
SEARCH_TOP_K = 5           # 返回结果数
XLSX_MAX_ROWS = 0          # Excel 最大读取行数（0=不限制）

HYBRID_ENABLED = True      # 混合检索开关
RRF_K = 60                 # 排名融合平滑常数
BM25_K1 = 1.2              # BM25 词频饱和度

LLM_MODEL_NAME = "deepseek-chat"  # LLM 回答模式使用的模型
```

## 检索模式

| 模式 | 适用场景 |
|------|---------|
| **混合检索**（默认） | BM25 命中精确词/编号 + 向量捕获语义关联 → RRF 融合 |
| **纯向量回退** | BM25 索引未构建时自动切换 |

每路召回 `top_k × 3` 个候选，RRF 融合后截取前 `top_k` 个结果。

## 已知限制

- 图片、图表、SmartArt 中的内容无法提取
- 不提取文档批注、修订痕迹、页眉页脚
- 旧格式需本机安装 Microsoft Office
- 语义检索对大范围列表型查询可能不完整，建议结合 `_索引.txt`

## 扩展新格式

1. 编写 `xxx2txt.py`，实现 `convert(source_path, dest_path) -> int`
2. 在 `archive.py` 的 `SUPPORTED_FORMATS` 添加一行：`".xxx": ("xxx2txt", "描述", True)`