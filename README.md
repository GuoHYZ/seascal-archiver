# 办公文档批量归档与语义检索工具 (Office2TXT + RAG)

将 PPTX、DOCX、XLSX 办公文档批量转换为纯文本 TXT（保留表格为 Markdown），并构建 Embedding 向量索引，支持语义检索。为 AI / RAG 知识库检索提供文本化档案。

## 功能特性

- **多格式支持**：PPTX（演示文稿）、DOCX（Word 文档）、XLSX（Excel 电子表格），以及 `.doc`/`.ppt`/`.xls` 旧格式（需本机 MS Office）
- **递归扫描**：自动处理多级子目录，镜像输出目录结构
- **表格保留**：Word/Excel 表格转为 Markdown 表格，PPT 表格提取文本
- **增量更新**：仅处理新增/修改的文件，SHA256 内容哈希，跨设备复制后仍准确
- **自动索引**：生成 `_索引.txt`，列出所有文档的格式、路径、字符数、源文件修改时间
- **语义检索（RAG）**：基于 BGE 中文 Embedding 模型的 FAISS 向量索引，自然语言搜索文档内容
- **GPU 加速**：支持 CUDA（RTX 4060 已验证），无 GPU 时自动切换 CPU
- **LLM 回答模式**：检索结果可喂给在线 LLM（DeepSeek/GPT 等），结合严格的反幻觉提示词生成答案
- **容错设计**：单文件失败不影响其他文件，单页异常不丢整文件
- **可扩展**：新增文档格式只需添加转换模块并注册一行

## 环境要求

- Python 3.7+（已在 3.13 + CUDA 12.4 验证）
- 依赖包（见 `requirements.txt`）
- 旧格式支持需本机安装 Microsoft Office
- GPU 加速需 NVIDIA 显卡 + CUDA（可选，CPU 也可运行）

## 安装

```bash
# 克隆或下载项目到本地
cd 脚本目录/

# 安装依赖
pip install -r requirements.txt
```

## 快速开始

### 第一步：准备文档

将需要转换的办公文档放入一个文件夹（支持多级嵌套）：

```
D:\公司文档\
├── 2023年\
│   ├── 年度汇报.pptx
│   └── 财务数据.xlsx
├── 2024年\
│   └── Q1\
│       ├── 项目方案.docx
│       └── 进度报告.pptx
└── 制度文件\
    └── 管理办法.docx
```

### 第二步：执行转换

```bash
# 全量转换（首次使用）
python archive.py D:\公司文档

# 自动输出到 D:\公司文档_TXT归档\
```

### 第三步：构建语义索引

```bash
# 为 TXT 归档构建向量索引
python build_rag.py D:\公司文档_TXT归档

# 或使用配置文件中的默认路径
python build_rag.py
```

### 第四步：语义检索

```bash
# 静默模式：只返回相关文本块及来源（--db 指定 TXT 归档目录）
python search_rag.py "铁塔维护费用是多少" --db D:\公司文档_TXT归档

# LLM 回答模式：检索 + 在线 LLM 生成完整回答
# （需在 rag_config.py 中配置 LLM_API_KEY）
python search_rag.py "2024年新建基站数量" --db D:\公司文档_TXT归档 --llm
```

### 日常维护（增量）

```bash
# 有新文档或修改旧文档时
python archive.py D:\公司文档 -i              # 增量转换
python build_rag.py D:\公司文档_TXT归档        # 增量重建索引

# 同时清理过时文件
python archive.py D:\公司文档 -i --clean
```

### 输出结果

```
D:\公司文档_TXT归档\
├── _索引.txt              ← 总索引文件
├── _archive_meta.json      ← 增量更新元数据（自动维护）
├── 2023年\
│   ├── 年度汇报.txt
│   └── 财务数据.txt        ← Markdown 表格格式
├── 2024年\
│   └── Q1\
│       ├── 项目方案.txt
│       └── 进度报告.txt
└── 制度文件\
    └── 管理办法.txt

脚本/
└── _faiss/                 ← FAISS 向量索引（自动生成，~10MB/100文档）
```

## 命令行参考

### 文档转换

| 命令 | 说明 |
|------|------|
| `python archive.py <目录>` | 全量转换，输出到 `目录名_TXT归档` |
| `python archive.py <目录> <输出>` | 指定输出路径 |
| `python archive.py <目录> -i` | 增量模式 |
| `python archive.py <目录> -i --clean` | 增量 + 清理过时 TXT |
| `python pptx2txt.py` | 独立使用：转换 `./PPT/` 下 PPTX |
| `python docx2txt.py` | 独立使用：转换 `./DOCX/` 下 DOCX |
| `python xlsx2txt.py` | 独立使用：转换 `./XLSX/` 下 XLSX |

### RAG 语义检索

| 命令 | 说明 |
|------|------|
| `python build_rag.py [TXT归档]` | 构建/更新向量索引 |
| `python build_rag.py [TXT归档] --force` | 强制重建索引 |
| `python search_rag.py "问题" --db <TXT归档>` | 语义检索（静默模式） |
| `python search_rag.py "问题" --db <TXT归档> --llm` | 检索 + LLM 回答 |

## 项目结构

```
脚本/
├── archive.py               ← 主入口（扫描、调度、索引生成、增量管理）
├── pptx2txt.py              ← PPTX → TXT 转换模块
├── docx2txt.py              ← DOCX → TXT 转换模块（段落 + 表格 → Markdown）
├── xlsx2txt.py              ← XLSX → TXT 转换模块（多 Sheet → Markdown 表格）
├── legacy2new.py            ← 旧格式转换（.doc/.ppt/.xls → .docx/.pptx/.xlsx，需 MS Office）
├── rag_config.py            ← RAG 全局配置（模型、切分、API）
├── build_rag.py             ← RAG 索引构建（TXT → 切块 → Embedding → Chroma）
├── search_rag.py            ← RAG 语义检索（自然语言搜索 + 可选 LLM 回答）
├── requirements.txt         ← Python 依赖清单
├── .gitignore               ← Git 忽略规则
├── README.md                ← 本文件
└── 文档库检索提示词.md        ← AI 检索系统指令（配置到 Agent/Dify 知识库）
```

### 架构说明

**文档转换管道**：每个转换模块遵循统一接口 `convert(source_path, dest_path) -> int`

```
archive.py (调度器)
  ├── scan_files()         → 递归扫描目录
  ├── legacy2new.convert() → 旧格式自动转新格式
  ├── _classify_files()    → SHA256 增量检测
  ├── _get_converter()     → 动态加载转换模块
  ├── pptx2txt.convert()
  ├── docx2txt.convert()
  ├── xlsx2txt.convert()
  └── _write_index()       → 生成 _索引.txt
```

**RAG 检索管道**：

```
build_rag.py                              search_rag.py
  TXT归档/  →  Chunker  →  Embedder       用户问题 → Embedder
               500字/块    (GPU/CPU)                   ↓
                           ↓                Chroma 向量搜索 (top5)
                          Chroma                      ↓
                          chroma_db/        相关块 + LLM → 最终回答
```

新增格式只需：编写转换模块 → 在 `archive.py` 的 `SUPPORTED_FORMATS` 注册表添加一行

## 跨设备使用

`TXT归档文件夹` 是一个完全自包含的便携文档库，可以直接复制到 U 盘、另一台电脑或上传到云端：

```
D:\公司文档_TXT归档\        ← 一个文件夹 = 完整文档库
├── _索引.txt              ← 总索引（修改时间、字符数等元信息）
├── _archive_meta.json      ← 增量更新记录
├── _faiss/                 ← FAISS 语义搜索向量索引
├── 2023年\
│   ├── 年度汇报.txt
│   └── 财务数据.txt
└── ...
```

- TXT 文件使用相对路径组织，无绝对路径依赖
- 增量检测基于 SHA256 内容哈希，不依赖文件时间戳
- `_chroma_db/` 与 TXT 同目录，整体移动无需任何适配

## 配合 AI 使用 — 三种模式

### 模式一：直接上传文件夹（最简方式）

适用于 ChatGPT、Claude、Dify 知识库等**支持上传文件夹或附件的 AI**：

1. 将 `TXT归档文件夹` 整体上传到 AI 知识库（作为附件或知识库来源）
2. 将 `文档库检索提示词.md` 中的「提示词正文」复制到系统指令
3. AI 会自动读取 `_索引.txt` 了解全貌，再按需深入检索具体 TXT 文件

> AI 会按照提示词中规定的流程工作：先读索引 → 锁定文件 → 读取全文 → 逐条标注来源。所有反幻觉规则同样生效。

### 模式二：本地 RAG Agent（功能最强）

适用于需要**语义搜索、跨文件关联、批量查询**的场景，且 PC 上有 Python 环境：

1. 完成上述「快速开始」中的四步操作（转换 → 构建索引）
2. 将 `文档库检索提示词.md` 配置为 Agent 的系统指令
3. Agent 会自动调用 `python search_rag.py "问题"` 进行语义检索，再调用在线 LLM 生成回答

### 模式三：Dify / Cherry Studio 等平台

将 `search_rag.py` 注册为自定义工具，`文档库检索提示词.md` 作为知识库的系统提示词。

提示词包含四层反幻觉机制：
- **源头隔离**：AI 只能读 TXT 文本，图片/图表不可见
- **工具约束**：语义检索返回相似度距离作为可信度指标
- **来源强制标注**：每条数据必须有 `【来源：文件名.txt】`
- **输出自检**：6 项幻觉自检 + 13 项操作检查

## 已知限制

- 图片、图表、SmartArt 中的内容无法提取
- 不提取文档批注、修订痕迹、页眉页脚
- 不提取嵌入对象（OLE）
- 旧格式（`.ppt` / `.doc` / `.xls`）需本机安装 Microsoft Office 才能自动转换
- 语义检索对大范围列表型查询（如"列出所有项目"）可能不完整，建议结合索引文件辅助

## 旧格式支持

脚本通过 `win32com` 调用本机 Microsoft Office 将旧格式自动转为新格式后处理：

| 旧格式 | 依赖 | 转换方式 |
|--------|------|---------|
| `.doc` | MS Word | `win32com` → `.docx` |
| `.ppt` | MS PowerPoint | `win32com` → `.pptx` |
| `.xls` | MS Excel | `win32com` → `.xlsx` |

未安装 Office 的设备上旧格式文件会被跳过。

## 性能参考

| 场景 | 时间（CPU） | 时间（GPU RTX 4060） |
|------|------------|---------------------|
| 归档 100 个文档 | 10-30 秒 | — |
| 构建索引（800 块） | 30-60 秒 | 5-10 秒 |
| 语义检索（单次） | 0.2-0.5 秒 | 0.05-0.1 秒 |
| 模型下载（首次） | ~2 GB | 一次性 |

## License

此项目为内部工具，无特定许可证。