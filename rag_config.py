# -*- coding: utf-8 -*-
"""
RAG 索引与检索 — 全局配置

所有可调参数集中在此，修改后 build_rag.py 和 search_rag.py 自动生效。
"""

from pathlib import Path

# ============================================================
# 路径配置
# ============================================================

# TXT 归档目录（archive.py 的输出目录）
# 可以是绝对路径或相对路径
# 推荐通过命令行传入: python build_rag.py ./TXT归档
TXT_ARCHIVE_DIR = Path(__file__).parent / "TXT归档"

# FAISS 向量索引子目录名（会创建在 TXT 归档目录内）
# 例如: D:\公司文档_TXT归档\_faiss/
FAISS_SUBDIR = "_faiss"

# 文档库检索提示词文件路径（作为 LLM system prompt）
PROMPT_FILE = Path(__file__).parent / "文档库检索提示词.md"

# ============================================================
# Embedding 模型配置
# ============================================================

# 后端选择: "local" | "openai" | "siliconflow"
EMBED_BACKEND = "local"

# ---- 本地模型配置 ----
# BGE-large-zh-v1.5: 中文语义搜索最优开源模型之一
# 首次运行自动从 HuggingFace 下载（~1.3GB），之后缓存
LOCAL_MODEL_NAME = "BAAI/bge-large-zh-v1.5"

# 运行设备: "cuda" | "cpu" | None（None=自动检测）
# - "cuda": 强制 GPU（需要 NVIDIA 显卡 + CUDA）
# - "cpu":  强制 CPU
# - None:   自动检测，有 GPU 就用 GPU
DEVICE = None  # 推荐 None，自动适配

# ---- 在线 API 配置（当 EMBED_BACKEND != "local" 时生效） ----
# 硅基流动 (siliconflow.cn): 免费额度，中文友好
# OpenAI: 付费，全球可用
EMBED_API_KEY = ""          # 在此填入 API Key，或设置环境变量 EMBED_API_KEY
EMBED_API_BASE = "https://api.siliconflow.cn/v1"  # 硅基流动
EMBED_MODEL_NAME = "BAAI/bge-large-zh-v1.5"       # 硅基流动上对应的模型名

# ============================================================
# 文本切分配置
# ============================================================

# 每块最大字符数
CHUNK_MAX_CHARS = 800

# 每块最小字符数（小于此值的块会与下一块合并）
CHUNK_MIN_CHARS = 200

# 块间重叠字符数（避免语义在边界断裂）
CHUNK_OVERLAP = 100

# ============================================================
# LLM 配置（search_rag.py --llm 模式使用）
# ============================================================

# 是否启用 LLM 回答模式
LLM_ENABLED = False

# LLM API 配置
LLM_API_KEY = ""            # 在此填入，或设环境变量 LLM_API_KEY
LLM_API_BASE = "https://api.deepseek.com/v1"  # DeepSeek 兼容 OpenAI 接口
LLM_MODEL_NAME = "deepseek-chat"              # 也可用 gpt-4o-mini / qwen-turbo 等

# 检索时返回的文本块数量
SEARCH_TOP_K = 5

# 每次发给 LLM 的最大上下文字符数
LLM_MAX_CONTEXT_CHARS = 8000